"""Flask app for exercising QuickBooks Online Sandbox operations.

Local development tool only: single user, localhost, Sandbox API only.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any, Optional

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for

import quickbooks_client as qbo
import token_store
from token_store import NoTokenError, RefreshTokenExpiredError, TokenData, TokenRefreshFailedError

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
if not FLASK_SECRET_KEY:
    raise RuntimeError(
        "FLASK_SECRET_KEY is not set. Copy .env.example to .env and set a random secret key."
    )
app.secret_key = FLASK_SECRET_KEY

QBO_CLIENT_ID = os.environ.get("INTUIT_CLIENT_ID", "")
QBO_CLIENT_SECRET = os.environ.get("INTUIT_CLIENT_SECRET", "")
QBO_REDIRECT_URI = os.environ.get("INTUIT_REDIRECT_URI", "http://localhost:8000/callback")
QBO_ENVIRONMENT = os.environ.get("QUICKBOOKS_ENVIRONMENT", "")

if QBO_ENVIRONMENT != "sandbox":
    raise RuntimeError(
        "QBO_ENVIRONMENT must be exactly 'sandbox'. This tool does not support production."
    )

DEFAULT_BILL_AMOUNT = 10


def _get_session() -> Optional[qbo.QuickBooksSession]:
    """Return an authenticated QuickBooksSession, or None if not connected."""
    if not QBO_CLIENT_ID or not QBO_CLIENT_SECRET:
        return None
    try:
        token = token_store.get_valid_token(QBO_CLIENT_ID, QBO_CLIENT_SECRET)
    except (NoTokenError, RefreshTokenExpiredError, TokenRefreshFailedError):
        return None
    return qbo.QuickBooksSession(token, QBO_CLIENT_ID, QBO_CLIENT_SECRET)


def _status_code_for(exc: qbo.QBOError) -> int:
    if exc.status_code and 400 <= exc.status_code < 600:
        return exc.status_code
    return 502


@app.route("/")
def index() -> str:
    qb_session = _get_session()
    token: Optional[TokenData] = qb_session.token if qb_session else None
    return render_template("index.html", connected=token is not None, realm_id=token.realm_id if token else None)


@app.route("/connect")
def connect() -> Any:
    if not QBO_CLIENT_ID or not QBO_CLIENT_SECRET:
        flash("尚未設定 QBO_CLIENT_ID / QBO_CLIENT_SECRET，請檢查 .env 檔案", "error")
        return redirect(url_for("index"))

    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    authorize_url = qbo.build_authorize_url(QBO_CLIENT_ID, QBO_REDIRECT_URI, state)
    return redirect(authorize_url)


@app.route("/callback")
def callback() -> Any:
    error = request.args.get("error")
    if error:
        flash(f"Intuit 回傳授權錯誤：{error}", "error")
        return redirect(url_for("index"))

    returned_state = request.args.get("state")
    expected_state = session.pop("oauth_state", None)
    if not expected_state or returned_state != expected_state:
        logger.warning("OAuth state mismatch on /callback; rejecting")
        flash("授權驗證失敗（state 不符），請重新連接", "error")
        return redirect(url_for("index"))

    code = request.args.get("code")
    realm_id = request.args.get("realmId")
    if not code or not realm_id:
        flash("callback 缺少 code 或 realmId 參數", "error")
        return redirect(url_for("index"))

    try:
        token_store.exchange_code(code, realm_id, QBO_REDIRECT_URI, QBO_CLIENT_ID, QBO_CLIENT_SECRET)
    except TokenRefreshFailedError as exc:
        logger.error("Token exchange failed: %s", exc)
        flash("無法完成 QuickBooks 授權，請稍後再試", "error")
        return redirect(url_for("index"))

    flash("已成功連接 QuickBooks Sandbox", "success")
    return redirect(url_for("index"))


@app.route("/company-info")
def company_info() -> Any:
    qb_session = _get_session()
    if qb_session is None:
        flash("請先連接 QuickBooks", "error")
        return redirect(url_for("index"))

    try:
        data = qb_session.get_company_info()
    except qbo.QBOAuthError:
        token_store.clear_token()
        flash("QuickBooks 連線已失效，請重新連接", "error")
        return redirect(url_for("index"))
    except qbo.QBOError as exc:
        return render_template("result.html", title="讀取公司資訊失敗", error=str(exc)), _status_code_for(exc)

    return render_template("result.html", title="公司資訊", data=data)


@app.route("/vendors")
def vendors() -> Any:
    qb_session = _get_session()
    if qb_session is None:
        flash("請先連接 QuickBooks", "error")
        return redirect(url_for("index"))

    try:
        items = qb_session.list_vendors()
    except qbo.QBOAuthError:
        token_store.clear_token()
        flash("QuickBooks 連線已失效，請重新連接", "error")
        return redirect(url_for("index"))
    except qbo.QBOError as exc:
        return render_template("result.html", title="讀取廠商清單失敗", error=str(exc)), _status_code_for(exc)

    return render_template("result.html", title="廠商清單", items=items, item_label_key="DisplayName")


@app.route("/accounts")
def accounts() -> Any:
    qb_session = _get_session()
    if qb_session is None:
        flash("請先連接 QuickBooks", "error")
        return redirect(url_for("index"))

    try:
        items = qb_session.list_expense_accounts()
    except qbo.QBOAuthError:
        token_store.clear_token()
        flash("QuickBooks 連線已失效，請重新連接", "error")
        return redirect(url_for("index"))
    except qbo.QBOError as exc:
        return render_template("result.html", title="讀取費用科目失敗", error=str(exc)), _status_code_for(exc)

    return render_template("result.html", title="費用科目清單", items=items, item_label_key="Name")


@app.route("/bill/new", methods=["GET", "POST"])
def new_bill() -> Any:
    qb_session = _get_session()
    if qb_session is None:
        flash("請先連接 QuickBooks", "error")
        return redirect(url_for("index"))

    try:
        vendors_list = qb_session.list_vendors()
        accounts_list = qb_session.list_expense_accounts()
    except qbo.QBOAuthError:
        token_store.clear_token()
        flash("QuickBooks 連線已失效，請重新連接", "error")
        return redirect(url_for("index"))
    except qbo.QBOError as exc:
        return render_template("result.html", title="載入表單資料失敗", error=str(exc)), _status_code_for(exc)

    if request.method == "GET":
        return render_template(
            "bill_form.html", vendors=vendors_list, accounts=accounts_list, default_amount=DEFAULT_BILL_AMOUNT
        )

    vendor_id = request.form.get("vendor_id", "")
    account_id = request.form.get("account_id", "")
    amount_raw = request.form.get("amount", str(DEFAULT_BILL_AMOUNT))
    memo = request.form.get("memo", "").strip()
    confirm_sandbox = request.form.get("confirm_sandbox")

    valid_vendor_ids = {v["Id"] for v in vendors_list}
    valid_account_ids = {a["Id"] for a in accounts_list}

    errors: list[str] = []
    if vendor_id not in valid_vendor_ids:
        errors.append("請選擇一個有效的廠商")
    if account_id not in valid_account_ids:
        errors.append("請選擇一個有效的費用科目")
    if not confirm_sandbox:
        errors.append("必須勾選「我確認這是 Sandbox 測試資料」才能送出")

    amount = 0.0
    try:
        amount = float(amount_raw)
        if amount <= 0:
            errors.append("金額必須大於 0")
    except ValueError:
        errors.append("金額格式不正確")

    if errors:
        return (
            render_template(
                "bill_form.html",
                vendors=vendors_list,
                accounts=accounts_list,
                default_amount=amount_raw,
                default_memo=memo,
                errors=errors,
                selected_vendor_id=vendor_id,
                selected_account_id=account_id,
            ),
            400,
        )

    try:
        bill = qb_session.create_bill(vendor_id, account_id, amount, memo)
    except qbo.QBOAuthError:
        token_store.clear_token()
        flash("QuickBooks 連線已失效，請重新連接", "error")
        return redirect(url_for("index"))
    except qbo.QBOError as exc:
        return render_template("result.html", title="建立 Bill 失敗", error=str(exc)), _status_code_for(exc)

    return render_template("result.html", title="Sandbox Bill 建立成功", data=bill)


if __name__ == "__main__":
    app.run(host="localhost", port=8000, debug=True)
