"""读取前端请求样例，调用平台接口，并输出可供前端查看的 JSON 响应。"""
from pathlib import Path
import json
from runoff_platform_api import (
    RequestValidationError, get_parameter_catalog, run_platform_request,
)

ROOT=Path(__file__).resolve().parent
REQUEST=ROOT/"example_request_land.json"
RESPONSE=ROOT/"example_response_land.json"
CATALOG=ROOT/"parameter_catalog_land.json"

if __name__=="__main__":
    try:
        payload=json.loads(REQUEST.read_text(encoding="utf-8"))
        response=run_platform_request(payload)
        RESPONSE.write_text(json.dumps(response,ensure_ascii=False,indent=2),encoding="utf-8")
        CATALOG.write_text(
            json.dumps(get_parameter_catalog("NON_GLACIER"),ensure_ascii=False,indent=2),
            encoding="utf-8",
        )
        print(f"status={response['status']}, records={response['metadata']['record_count']}")
        print(f"response={RESPONSE}")
        print(f"parameter_catalog={CATALOG}")
    except RequestValidationError as exc:
        print(json.dumps({"status":"VALIDATION_FAILED","errors":exc.errors},ensure_ascii=False,indent=2))
        raise SystemExit(2)
