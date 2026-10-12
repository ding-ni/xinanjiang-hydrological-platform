"""验证统一前端任务服务的状态机、结果、停止、失败、归档和持久化。"""

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("UI_BASE_URL", "http://127.0.0.1:5173")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.add_init_script(
        """if (!sessionStorage.getItem('task_test_initialized')) {
          localStorage.clear();
          localStorage.setItem('hydro_basin_id', 'selinco');
          sessionStorage.setItem('task_test_initialized', '1');
        }"""
    )
    page.goto(f"{BASE_URL}/#/forecast", wait_until="networkidle")

    run_button = page.get_by_role("button", name="开始预报", exact=True).first
    run_button.click()
    panel = page.locator(".task-status")
    panel.wait_for()
    page.wait_for_function(
        "() => document.querySelector('.task-status')?.classList.contains('completed')"
    )
    assert "试算任务" in panel.inner_text()
    assert "100%" in panel.inner_text()

    persisted = page.evaluate(
        """() => JSON.parse(localStorage.getItem('hydro_fixture_tasks_v1') || '{}')"""
    )
    forecast_tasks = [task for task in persisted.get("tasks", []) if task["runType"] == "forecast"]
    assert forecast_tasks and forecast_tasks[0]["status"] == "completed", forecast_tasks
    assert persisted.get("results", {}).get(forecast_tasks[0]["taskId"]), persisted

    page.locator('.sidebar a[href="#/archive"]').click()
    page.wait_for_url("**/#/archive")
    page.locator(".task-ledger").wait_for()
    assert forecast_tasks[0]["taskId"] in page.locator(".task-ledger").inner_text()
    assert "洪水预报" in page.locator(".task-ledger").inner_text()

    page.locator('.sidebar a[href="#/state"]').click()
    page.wait_for_url("**/#/state")
    page.get_by_role("button", name="续算状态", exact=True).click()
    page.get_by_role("button", name="开始续算", exact=True).click()
    page.wait_for_function(
        "() => document.querySelector('.state-task-panel .task-status')?.classList.contains('completed')"
    )
    assert "状态续算" in page.locator(".state-task-panel .task-status").inner_text()
    assert page.locator(".node-list").inner_text().count("2026-06-02 14:00") >= 1

    service_check = page.evaluate(
        """async () => {
          const { taskService } = await import('/src/services/taskService.js')
          const failed = await taskService.submitTask('continuousSimulation', {
            contextKey: 'selinco:selinco-inflow-lake:selinco-1km',
            domainId: 'selinco',
            fixtureScenarioId: 'failed'
          })
          let failedTask
          do {
            await new Promise(resolve => setTimeout(resolve, 80))
            failedTask = await taskService.getTask(failed.taskId)
          } while (!['failed', 'cancelled', 'completed'].includes(failedTask.status))

          const cancellable = await taskService.submitTask('stateContinuation', {
            contextKey: 'selinco:selinco-inflow-lake:selinco-1km',
            domainId: 'selinco',
            targetTime: '2026-06-02 14:00'
          })
          await new Promise(resolve => setTimeout(resolve, 140))
          const cancelled = await taskService.cancelTask(cancellable.taskId)
          const archived = await taskService.archiveTask(failedTask.taskId)
          return {
            failedStatus: failedTask.status,
            failedMessage: failedTask.errorMessage,
            cancelledStatus: cancelled.status,
            failedArchiveStatus: archived.status
          }
        }"""
    )
    assert service_check["failedStatus"] == "failed", service_check
    assert service_check["failedMessage"], service_check
    assert service_check["cancelledStatus"] == "cancelled", service_check
    assert service_check["failedArchiveStatus"] == "failed", service_check

    page.reload(wait_until="networkidle")
    restored = page.evaluate(
        """() => JSON.parse(localStorage.getItem('hydro_fixture_tasks_v1') || '{}').tasks || []"""
    )
    statuses = {task["status"] for task in restored}
    assert "completed" in statuses and "failed" in statuses and "cancelled" in statuses, restored

    browser.close()
    print("Task system smoke passed: lifecycle, result, cancel, failure and persistence are active")
