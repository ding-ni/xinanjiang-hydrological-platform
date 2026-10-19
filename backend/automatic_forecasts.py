"""Persistent hourly QPF scheduling. The server, not the browser, owns the loop."""
from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timedelta, timezone

from common import now, read_json, write_json, safe_task
from hydrological_operations import ACTIVE, CONTEXT
from input_products import hourly_time


def beijing_hour():
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


class AutomaticForecasts:
    def __init__(self, manager, *, clock=beijing_hour, start_thread=True):
        self.manager, self.clock = manager, clock
        self.root = manager.runtime_root / 'automatic-forecasts'
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.thread = None
        if start_thread:
            self.thread = threading.Thread(target=self.run, name='automatic-forecasts', daemon=True)
            self.thread.start()

    def get(self, identifier):
        if not str(identifier).startswith('AUTO-'):
            raise ValueError('自动预报编号无效')
        return read_json(safe_task(self.root, identifier) / 'schedule.json')

    def list(self):
        return {'schedules': sorted([read_json(p) for p in self.root.glob('*/schedule.json')],
                                    key=lambda r: r['createdAt'], reverse=True)}

    def issues(self, identifier):
        self.get(identifier)
        return {'issues': sorted([read_json(p) for p in safe_task(self.root, identifier).glob('issues/*.json')],
                                 key=lambda r: r['issueTime'], reverse=True)[:100]}

    def update(self, row, **changes):
        updated = {**row, **changes}
        if updated != row:
            updated['updatedAt'] = now()
            write_json(safe_task(self.root, row['id']) / 'schedule.json', updated)
        return updated

    def create(self, body):
        allowed = {'parameterSchemeId', 'sourceId', 'firstIssueTime', 'issueIntervalHours', 'leadHours', 'context', 'requestId'}
        if not isinstance(body, dict) or set(body)-allowed:
            raise ValueError('请设置已保存参数、降雨来源、首次起报、间隔和预见期')
        parameter = self.manager.parameter_schemes.get(body.get('parameterSchemeId'))
        if parameter['context'] != CONTEXT or body.get('context', CONTEXT) != CONTEXT:
            raise ValueError('当前自动预报对应南安溪全流域1 km模型')
        first = hourly_time(body.get('firstIssueTime'))
        interval, lead = body.get('issueIntervalHours', 1), body.get('leadHours', 24)
        if type(interval) is not int or interval < 1:
            raise ValueError('起报间隔须为正整数小时')
        if type(lead) is not int or not 1 <= lead <= 72:
            raise ValueError('预见期须为1–72小时整数')
        source = body.get('sourceId')
        if not any(r.get('sourceId', r['versionId']) == source for r in self.manager.forecast_products.list()):
            raise ValueError('请先导入并选择降雨产品来源')
        defaults = copy.deepcopy(self.manager.schemes.history_defaults())
        from station_network import local_station_network
        defaults['stationNetwork'] = local_station_network(self.manager.database)
        with self.lock:
            for row in self.list()['schedules']:
                if body.get('requestId') and row.get('createRequest', {}).get('requestId') == body['requestId']:
                    if row['createRequest'] != body:
                        raise ValueError('同次启动的预报设置已改变')
                    return row
                if row['enabled'] and row['context'] == parameter['context']:
                    raise ValueError('本流域已有自动预报，请先停止后更换设置')
            identifier = 'AUTO-' + uuid.uuid4().hex
            row = {'id': identifier, 'context': CONTEXT, 'createdAt': now(), 'enabled': True,
                   'parameterSchemeId': parameter['id'], 'parameterName': parameter['name'],
                   'parameterSnapshot': parameter, 'preparationDefaults': defaults, 'createRequest': body,
                   'sourceId': source, 'firstIssueTime': first.isoformat(), 'nextIssueTime': first.isoformat(),
                   'issueIntervalHours': interval, 'leadHours': lead, 'status': 'waiting',
                   'message': '等待起报', 'activeOperationId': None, 'latestOperationId': None,
                   'observationStateVersionId': None, 'completedCount': 0, 'attemptId': uuid.uuid4().hex,
                   'retryAfter': None, 'issues': [], 'runMode': 'automatic', 'catchUpUntil': None,
                   'latestIssueTime': None}
            write_json(safe_task(self.root, identifier) / 'schedule.json', row)
            return row

    def control(self, identifier, action):
        if action not in {'start', 'stop', 'catch-up'}:
            raise ValueError('请选择开始、关闭或补做预报')
        with self.lock:
            row = self.get(identifier)
            if action == 'stop':
                row = self.update(row, enabled=False, status='stopped', message='已关闭自动预报')
                if row.get('activeOperationId'):
                    self.manager.operations.cancel(row['activeOperationId'])
            else:
                if row['enabled']:
                    if action == 'catch-up':
                        raise ValueError('请先关闭自动预报，再补做空档')
                    return row
                if any(r['enabled'] and r['id'] != identifier and r['context'] == row['context']
                       for r in self.list()['schedules']):
                    raise ValueError('本流域已有自动预报，请先停止')
                # Freeze the upper bound once: catch-up is finite even while
                # the clock advances or the process restarts.
                until = self.clock().replace(minute=0, second=0, microsecond=0)
                if action == 'catch-up' and hourly_time(row['nextIssueTime']) > until:
                    raise ValueError('没有需要补做的起报时刻')
                row = self.update(row, enabled=True, status='waiting', message='等待续接实测状态', retryAfter=None,
                    runMode='catchup' if action == 'catch-up' else 'automatic',
                    catchUpUntil=until.isoformat() if action == 'catch-up' else None)
            return row

    def tick(self):
        with self.lock:
            for row in self.list()['schedules']:
                if not row['enabled'] and not row.get('activeOperationId'):
                    continue
                try:
                    self.advance(row)
                except Exception as error:
                    # One broken source must not kill the scheduler or other jobs.
                    self.update(self.get(row['id']), status='waiting' if row['enabled'] else 'stopped', message=str(error),
                                retryAfter=(self.clock()+timedelta(minutes=1)).isoformat())

    def advance(self, row):
        operation = row.get('activeOperationId')
        if operation:
            task = self.manager.operations.get(operation)
            if task['status'] in ACTIVE:
                if row['enabled']:
                    self.update(row, status='running', message=task.get('stageLabel', '正在计算'))
                return
            if task['status'] == 'completed':
                result = self.manager.operations.result(operation)
                issue = row['nextIssueTime']
                write_json(safe_task(self.root, row['id']) / 'issues' / (issue.replace(':', '-')+'.json'),
                           {'issueTime': issue, 'operationId': operation, 'taskId': result['primaryTaskId'],
                            'observationStateVersionId': result['observationStateVersionId'],
                            'forecastRainVersionId': result['forecastRain']['versionId'],
                            'purpose': row.get('runMode', 'automatic')})
                row = self.update(row, activeOperationId=None, latestOperationId=operation,
                    observationStateVersionId=result['observationStateVersionId'],
                    nextIssueTime=(hourly_time(issue)+timedelta(hours=row['issueIntervalHours'])).isoformat(),
                    completedCount=row['completedCount']+1, latestIssueTime=issue, retryAfter=None, issues=[],
                    attemptId=uuid.uuid4().hex, status='waiting' if row['enabled'] else 'stopped',
                    message='等待下一次起报' if row['enabled'] else '已关闭自动预报')
            else:
                # Interrupted/cancelled jobs retry with a new attempt identity. A
                # crash after submit but before saving activeOperationId reuses it.
                self.update(row, activeOperationId=None, status='waiting' if row['enabled'] else 'stopped',
                    observationStateVersionId=task.get('observationStateVersionId') or row.get('observationStateVersionId'),
                    issues=task.get('issues', []), message=(task.get('errorMessage') or '上次计算中断，等待重试') if row['enabled'] else '已关闭自动预报',
                    retryAfter=(self.clock()+timedelta(minutes=1)).isoformat(), attemptId=uuid.uuid4().hex)
                return
        if not row['enabled']:
            return
        if row.get('runMode') == 'catchup' and hourly_time(row['nextIssueTime']) > hourly_time(row['catchUpUntil']):
            self.update(row, enabled=False, status='stopped', message='补做完成 · 已补至 '+row['catchUpUntil'].replace('T', ' ')[:16])
            return
        current = self.clock()
        if row.get('retryAfter') and datetime.fromisoformat(row['retryAfter']) > current:
            return
        if hourly_time(row['nextIssueTime']) > current:
            self.update(row, status='waiting', message='等待起报时刻（北京时间）')
            return
        product = self.manager.forecast_products.latest(row['sourceId'], row['nextIssueTime'], row['leadHours'])
        if product is None:
            self.update(row, status='waiting', message='等待降雨产品覆盖 '+row['nextIssueTime'].replace('T', ' ')[:16]+' 起报的完整预见期；历史起报需对应的历史产品')
            return
        # Persist the exact payload before submission for restart idempotency.
        submission = row.get('pendingSubmission')
        if not submission or submission['requestId'] != row['attemptId']:
            submission = {'operation': 'automaticForecast', 'requestId': row['attemptId'],
                'parameterSchemeId': row['parameterSchemeId'], 'context': row['context'],
                'issueTime': row['nextIssueTime'], 'leadHours': row['leadHours'],
                'forecastRainVersionId': product['versionId'],
                'observationStateVersionId': row.get('observationStateVersionId'),
                'name': ('补做预报' if row.get('runMode') == 'catchup' else '自动预报')+' · '+row['nextIssueTime'].replace('T', ' ')}
            row = self.update(row, pendingSubmission=submission)
        task = self.manager.operations.submit(submission, preparation_defaults=row['preparationDefaults'],
                                               parameter_snapshot=row['parameterSnapshot'])
        self.update(row, activeOperationId=task['taskId'], status='running', message='正在准备起报', retryAfter=None)

    def run(self):
        while not self.closed.wait(2):
            try:
                self.tick()
            except Exception:
                # Persisted malformed schedules can be diagnosed via HTTP/logs;
                # do not silently turn off other schedules.
                import traceback
                traceback.print_exc()

    def close(self):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout=10)
