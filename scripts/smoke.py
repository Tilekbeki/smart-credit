"""Exercise the synthetic client journey through the public frontend proxy.

Each run creates one new draft for each of two seeded clients. It never accepts
an offer or moves money, and it does not use personal or production data.
"""

import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


BASE_URL = os.environ.get("SMART_CREDIT_URL", "http://127.0.0.1:8081").rstrip("/")
OTP_CODE = os.environ.get("SMART_CREDIT_OTP_CODE", "1221")
ALFA_CLIENT_PHONE = "+79990000001"  # Trusted synthetic bank feed.
EXTERNAL_CLIENT_PHONE = "+79990000002"  # Requires BANK_STATEMENT.
PDF_FIXTURE = Path(__file__).resolve().parent.parent / "backend/api/postman/fixtures/sample.pdf"
FINAL_STATUSES = {"approved", "counter_offer", "rejected", "manual_review"}
APPLICATION_INPUT = {
    "productId": "turnover",
    "purpose": "stock",
    "requestedAmountRub": 1_500_000,
    "termMonths": 12,
    "monthlyRevenueRub": 2_400_000,
    "monthlyExpensesRub": 1_500_000,
}
EXTERNAL_APPLICATION_INPUT = {
    **APPLICATION_INPUT,
    "monthlyRevenueRub": 4_200_000,
    "monthlyExpensesRub": 2_800_000,
}


class SmokeError(Exception):
    """A failed API expectation with a concise, safe message."""


def require(condition, message):
    if not condition:
        raise SmokeError(message)


def call(method, path, body=None, token=None, expected=200):
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    if method == "POST" and payload is None:
        payload = b""  # Submit requires an empty request body.
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(BASE_URL + path, data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            data = json.load(response)
            require(response.status == expected, f"{method} {path}: HTTP {response.status}, ожидался {expected}")
            require(isinstance(data, dict), f"{method} {path}: ожидался JSON-объект")
            return data
    except HTTPError as error:
        try:
            details = json.load(error)
        except (ValueError, OSError):
            details = {}
        if error.code == expected:
            require(isinstance(details, dict), f"{method} {path}: ожидался JSON-объект ошибки")
            return details
        code = details.get("error", "http_error") if isinstance(details, dict) else "http_error"
        message = details.get("message", "") if isinstance(details, dict) else ""
        raise SmokeError(f"{method} {path}: HTTP {error.code} {code}: {message}") from error


def upload_statement(application_id, token):
    """Upload the backend's small, explicitly synthetic PDF fixture as BANK_STATEMENT."""
    try:
        pdf = PDF_FIXTURE.read_bytes()
    except OSError as error:
        raise SmokeError("Синтетический PDF не найден; проверьте backend submodule") from error
    require(pdf.startswith(b"%PDF-") and b"Synthetic fixture: no banking data" in pdf,
            "Синтетический PDF fixture не найден или изменён")
    boundary = f"smart-credit-smoke-{uuid4().hex}"
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="documentType"\r\n\r\n'
        "BANK_STATEMENT\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="synthetic-bank-statement.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("ascii")
    body = prefix + pdf + f"\r\n--{boundary}--\r\n".encode("ascii")
    path = f"/api/v1/applications/{application_id}/documents"
    request = Request(
        BASE_URL + path, data=body, method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            require(response.status == 201, f"POST {path}: HTTP {response.status}, ожидался 201")
            document = json.load(response)
    except HTTPError as error:
        try:
            details = json.load(error)
        except (ValueError, OSError):
            details = {}
        code = details.get("error", "http_error") if isinstance(details, dict) else "http_error"
        message = details.get("message", "") if isinstance(details, dict) else ""
        raise SmokeError(f"POST {path}: HTTP {error.code} {code}: {message}") from error
    require(isinstance(document, dict) and document.get("applicationId") == application_id,
            "Загрузка выписки вернула другую заявку")
    require(document.get("documentType") == "BANK_STATEMENT" and document.get("contentType") == "application/pdf",
            "Загруженный файл не распознан как BANK_STATEMENT PDF")
    document_id = document.get("documentId")
    require(isinstance(document_id, str) and document_id, "Загрузка выписки не вернула documentId")
    listing = call("GET", path, token=token)
    items = listing.get("items")
    require(isinstance(items, list) and any(item.get("documentId") == document_id for item in items),
            "Выписка не отображается в сохранённых документах")
    print("Синтетическая PDF-выписка загружена и доступна в кабинете")
    return document_id


def login(profile_id):
    challenge = call("POST", "/api/v1/auth/otp/request", {"profileId": profile_id}, expected=201)
    challenge_id = challenge.get("otpRequestId")
    require(isinstance(challenge_id, str) and challenge_id, "OTP challenge не вернул otpRequestId")
    session = call(
        "POST", "/api/v1/auth/otp/verify",
        {"otpRequestId": challenge_id, "code": OTP_CODE},
    )
    token = session.get("accessToken")
    require(isinstance(token, str) and token, "OTP verify не вернул accessToken")
    require(session.get("role") == "CLIENT", "Тестовый профиль не имеет роль CLIENT")
    return token


def wait_for_decision(application_id, token, poll_seconds):
    deadline = time.monotonic() + poll_seconds
    last_status = None
    last_stage = None
    path = f"/api/v1/applications/{application_id}/decision"
    while True:
        decision = call("GET", path, token=token)
        status = decision.get("status")
        processing = decision.get("processing")
        stage = processing.get("stage") if isinstance(processing, dict) else None
        if (status, stage) != (last_status, last_stage):
            print(f"Обработка: {status}" + (f" / {stage}" if stage else ""))
            last_status, last_stage = status, stage
        if status in FINAL_STATUSES:
            return decision
        require(status in {"submitted", "processing"}, f"Неожиданный статус обработки: {status}")
        if time.monotonic() >= deadline:
            raise SmokeError(f"Решение не получено за {poll_seconds:g} с; последнее состояние: {status}/{stage}")
        time.sleep(0.75)


def appears_in_cabinet(application_id, token):
    offset = 0
    while True:
        page = call("GET", f"/api/v1/applications?limit=100&offset={offset}", token=token)
        items = page.get("items")
        require(isinstance(items, list), "Список заявок не вернул items")
        if any(item.get("applicationId") == application_id for item in items if isinstance(item, dict)):
            return True
        offset += len(items)
        total = page.get("total")
        require(isinstance(total, int) and total >= 0, "Список заявок не вернул total")
        if not items or offset >= total:
            return False


def run_scenario(profile, draft_input, needs_statement, poll_seconds):
    token = login(profile["id"])
    user = call("GET", "/api/v1/me", token=token)
    require(user.get("id") == profile["id"], "Вход выполнен под другим профилем")
    print(f"\nВход: {profile['companyName']} (синтетический профиль)")

    draft = call("POST", "/api/v1/applications", draft_input, token, expected=201)
    application_id = draft.get("applicationId")
    require(isinstance(application_id, str) and application_id, "Создание черновика не вернуло applicationId")
    require(draft.get("status") == "draft", "Новая заявка должна быть черновиком")
    print(f"Черновик: {application_id}")

    submit_path = f"/api/v1/applications/{application_id}/submit"
    document_id = None
    if needs_statement:
        validation = call("POST", submit_path, token=token, expected=422)
        details = validation.get("details")
        require(validation.get("error") == "validation_failed" and isinstance(details, list),
                "Отправка без выписки не вернула ожидаемую ошибку валидации")
        require(any(isinstance(item, dict) and item.get("field") == "documents.BANK_STATEMENT"
                    and item.get("code") == "required_document_missing" for item in details),
                "Backend не потребовал BANK_STATEMENT для внешнего клиента")
        unchanged = call("GET", f"/api/v1/applications/{application_id}", token=token)
        require(unchanged.get("status") == "draft" and unchanged.get("version") == draft.get("version"),
                "Неуспешная отправка изменила черновик")
        print("Отправка без выписки: ожидаемая ошибка 422, черновик сохранён")
        document_id = upload_statement(application_id, token)

    submitted = call("POST", submit_path, token=token, expected=202)
    require(submitted.get("applicationId") == application_id, "Submit вернул другую заявку")
    require(submitted.get("status") in {"submitted", "processing"}, "Submit не запустил обработку")
    print("Отправка: принята backend")

    decision = wait_for_decision(application_id, token, poll_seconds)
    score = decision.get("score")
    require(isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 100,
            "Итоговое состояние не содержит оценку скоринга")
    if decision["status"] == "manual_review":
        require(decision.get("decision") is None, "Ручная проверка не должна показывать финальное решение")
    else:
        require(decision.get("decision") == decision["status"], "Тип решения не соответствует статусу")
    timeline = call("GET", f"/api/v1/applications/{application_id}/timeline", token=token)
    events = timeline.get("items")
    require(isinstance(events, list), "История заявки не вернула items")
    event_types = {event.get("eventType") for event in events if isinstance(event, dict)}
    require({"APPLICATION_SUBMITTED", "SCORING_STARTED", "SCORING_COMPLETED"} <= event_types,
            "История не подтверждает завершённый вызов Python scoring")
    print(f"Скоринг: завершён, балл {score}; итог {decision['status']}")

    # A fresh OTP/JWT proves that the cabinet reads server state, not browser memory.
    token = login(profile["id"])
    restored = call("GET", f"/api/v1/applications/{application_id}", token=token)
    restored_decision = call("GET", f"/api/v1/applications/{application_id}/decision", token=token)
    require(restored.get("applicationId") == application_id and restored.get("status") == decision["status"],
            "Повторный вход не восстановил заявку")
    require(restored_decision.get("score") == score and restored_decision.get("status") == decision["status"],
            "Повторный вход не восстановил решение")
    require(appears_in_cabinet(application_id, token), "Заявка не отображается в списке кабинета")
    if document_id:
        documents = call("GET", f"/api/v1/applications/{application_id}/documents", token=token).get("items")
        require(isinstance(documents, list) and any(isinstance(item, dict) and item.get("documentId") == document_id
                                                    for item in documents),
                "Повторный вход не восстановил выписку")
    print("Повторный вход: заявка и решение сохранены и доступны в кабинете")


def main():
    poll_seconds = float(os.environ.get("SMART_CREDIT_POLL_SECONDS", "60"))
    require(0 < poll_seconds <= 600, "SMART_CREDIT_POLL_SECONDS должен быть от 1 до 600")
    profiles = call("GET", "/api/v1/demo/profiles").get("items")
    require(isinstance(profiles, list), "Сервис не вернул тестовые профили")
    for phone, draft_input, needs_statement in (
        (ALFA_CLIENT_PHONE, APPLICATION_INPUT, False),
        (EXTERNAL_CLIENT_PHONE, EXTERNAL_APPLICATION_INPUT, True),
    ):
        clients = [profile for profile in profiles if isinstance(profile, dict)
                   and profile.get("phone") == phone and profile.get("role") == "CLIENT"]
        require(len(clients) == 1, f"Не найден синтетический CLIENT с номером {phone}")
        run_scenario(clients[0], draft_input, needs_statement, poll_seconds)
    print("\nОба сквозных сценария frontend proxy → Go backend → Python scoring пройдены.")


if __name__ == "__main__":
    try:
        main()
    except (SmokeError, URLError, TimeoutError, ValueError, KeyError) as error:
        print(f"Smoke test failed: {error}", file=sys.stderr)
        sys.exit(1)
