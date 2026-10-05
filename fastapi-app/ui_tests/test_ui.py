import os
import re
from datetime import date, timedelta
from pathlib import Path

import httpx2
import pytest
from playwright.sync_api import Page, expect

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
SHOT_DIR = Path(os.environ.get("UI_SHOT_DIR", "ui_report/screenshots"))
CDN_PATTERN = re.compile(r"cdn\.jsdelivr\.net")


@pytest.fixture(autouse=True)
def clean_server_state():
    with httpx2.Client(base_url=BASE_URL, timeout=10) as api:
        for todo in api.get("/todos").json():
            api.delete(f"/todos/{todo['id']}")
    yield


@pytest.fixture(autouse=True)
def open_app(page: Page):
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    yield


def snap(page: Page, name: str):
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOT_DIR / f"{name}.png", full_page=True)


def add_todo(page: Page, title: str, description: str = ""):
    page.fill("#new-title", title)
    if description:
        page.fill("#new-desc", description)
    page.click("#add-btn")
    expect(page.locator("#list li", has_text=title)).to_be_visible()


def row(page: Page, title: str):
    return page.locator("#list li", has_text=title)


# ---------- 화면 기본 ----------

def test_page_shows_title_and_empty_state(page: Page):
    expect(page).to_have_title("To-Do")
    expect(page.locator("h1")).to_have_text("To-Do")
    expect(page.locator(".empty")).to_contain_text("할 일이 없습니다")
    snap(page, "01_empty_state")


# ---------- 추가 ----------

def test_add_todo_appears_in_list_and_updates_count(page: Page):
    add_todo(page, "우유 사기", "저지방")
    expect(row(page, "우유 사기").locator(".title")).to_have_text("우유 사기")
    expect(row(page, "우유 사기").locator(".desc")).to_have_text("저지방")
    expect(page.locator("#count")).to_have_text("해야 할 일 1개 (총 1개)")
    expect(page.locator("#new-title")).to_have_value("")
    snap(page, "02_after_add")


def test_add_todo_with_enter_key(page: Page):
    page.fill("#new-title", "엔터로 추가")
    page.press("#new-title", "Enter")
    expect(row(page, "엔터로 추가")).to_be_visible()


def test_add_without_title_shows_error(page: Page):
    page.click("#add-btn")
    expect(page.locator("#error")).to_be_visible()
    expect(page.locator("#error")).to_contain_text("제목을 입력해 주세요")
    expect(page.locator("#list li")).to_have_count(0)
    snap(page, "03_validation_error")


# ---------- 완료 체크 ----------

def test_check_marks_done_and_updates_count(page: Page):
    add_todo(page, "운동하기")
    row(page, "운동하기").locator("input[type=checkbox]").check()
    expect(row(page, "운동하기").locator(".title")).to_have_class(re.compile(r"\bdone\b"))
    expect(page.locator("#count")).to_have_text("해야 할 일 0개 (총 1개)")
    snap(page, "04_checked")


def test_uncheck_removes_done_state(page: Page):
    add_todo(page, "책 읽기")
    checkbox = row(page, "책 읽기").locator("input[type=checkbox]")
    checkbox.check()
    checkbox.uncheck()
    expect(row(page, "책 읽기").locator(".title")).not_to_have_class(re.compile(r"\bdone\b"))
    expect(page.locator("#count")).to_have_text("해야 할 일 1개 (총 1개)")


def test_check_triggers_confetti_canvas(page: Page):
    add_todo(page, "폭죽 확인")
    row(page, "폭죽 확인").locator("input[type=checkbox]").check()
    expect(page.locator("canvas").first).to_be_attached(timeout=5000)
    page.wait_for_timeout(600)
    snap(page, "05_confetti")


def test_check_still_saves_when_cdn_blocked(page: Page):
    page.route(CDN_PATTERN, lambda route: route.abort())
    page.reload()
    page.wait_for_load_state("domcontentloaded")
    add_todo(page, "CDN 차단 상황")
    row(page, "CDN 차단 상황").locator("input[type=checkbox]").check()
    page.reload()
    expect(row(page, "CDN 차단 상황").locator("input[type=checkbox]")).to_be_checked()


# ---------- 수정 ----------

def test_edit_title_saves(page: Page):
    add_todo(page, "예전 제목")
    row(page, "예전 제목").get_by_role("button", name="수정").click()
    page.locator("input.title-edit").first.fill("새 제목")
    page.get_by_role("button", name="저장").click()
    expect(row(page, "새 제목")).to_be_visible()
    expect(page.locator("#list li", has_text="예전 제목")).to_have_count(0)
    snap(page, "06_after_edit")


def test_edit_escape_cancels(page: Page):
    add_todo(page, "취소할 제목")
    row(page, "취소할 제목").get_by_role("button", name="수정").click()
    page.locator("input.title-edit").first.fill("바뀌면 안 됨")
    page.locator("input.title-edit").first.press("Escape")
    expect(row(page, "취소할 제목")).to_be_visible()
    expect(page.locator("#list li", has_text="바뀌면 안 됨")).to_have_count(0)


def test_edit_empty_title_shows_error(page: Page):
    add_todo(page, "빈 제목 테스트")
    row(page, "빈 제목 테스트").get_by_role("button", name="수정").click()
    page.locator("input.title-edit").first.fill("   ")
    page.get_by_role("button", name="저장").click()
    expect(page.locator("#error")).to_contain_text("제목은 비워둘 수 없습니다")


# ---------- 우선순위 ----------

def test_move_down_reorders_list(page: Page):
    add_todo(page, "첫번째")
    add_todo(page, "두번째")
    row(page, "첫번째").locator("button[title='우선순위 내리기']").click()
    expect(page.locator("#list li .title").nth(0)).to_have_text("두번째")
    expect(page.locator("#list li .title").nth(1)).to_have_text("첫번째")
    snap(page, "07_after_move_down")


def test_move_up_reorders_list(page: Page):
    add_todo(page, "가")
    add_todo(page, "나")
    row(page, "나").locator("button[title='우선순위 올리기']").click()
    expect(page.locator("#list li .title").nth(0)).to_have_text("나")


def test_move_buttons_disabled_at_edges(page: Page):
    add_todo(page, "A")
    add_todo(page, "B")
    expect(row(page, "A").locator("button[title='우선순위 올리기']")).to_be_disabled()
    expect(row(page, "B").locator("button[title='우선순위 내리기']")).to_be_disabled()


def test_move_persists_after_reload(page: Page):
    add_todo(page, "X")
    add_todo(page, "Y")
    row(page, "X").locator("button[title='우선순위 내리기']").click()
    expect(page.locator("#list li .title").nth(0)).to_have_text("Y")
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#list li .title").nth(0)).to_have_text("Y")


# ---------- 삭제 / 상태 유지 ----------

def test_delete_removes_item(page: Page):
    add_todo(page, "지울 항목")
    row(page, "지울 항목").get_by_role("button", name="삭제").click()
    expect(page.locator("#list li", has_text="지울 항목")).to_have_count(0)
    expect(page.locator(".empty")).to_be_visible()
    snap(page, "08_after_delete")


def test_items_persist_after_reload(page: Page):
    add_todo(page, "새로고침 전")
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(row(page, "새로고침 전")).to_be_visible()
    expect(page.locator("#count")).to_have_text("해야 할 일 1개 (총 1개)")


# ---------- 마감일 ----------

def add_todo_with_due(page: Page, title: str, due: str):
    page.fill("#new-title", title)
    page.fill("#new-due", due)
    page.click("#add-btn")
    expect(row(page, title)).to_be_visible()


def test_due_date_is_shown_on_row(page: Page):
    add_todo_with_due(page, "보고서 제출", "2099-12-31")
    due = row(page, "보고서 제출").locator(".due")
    expect(due).to_have_text("마감: 2099-12-31")
    expect(due).not_to_have_class(re.compile(r"\boverdue\b"))
    snap(page, "09_due_date")


def test_overdue_due_date_is_highlighted(page: Page):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    add_todo_with_due(page, "지난 일정", yesterday)
    due = row(page, "지난 일정").locator(".due")
    expect(due).to_have_class(re.compile(r"\boverdue\b"))
    expect(due).to_contain_text("기한 초과")
    snap(page, "10_overdue")


def test_done_item_is_not_marked_overdue(page: Page):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    add_todo_with_due(page, "끝난 일정", yesterday)
    row(page, "끝난 일정").locator("input[type=checkbox]").check()
    expect(row(page, "끝난 일정").locator(".due")).not_to_have_class(re.compile(r"\boverdue\b"))


def test_edit_due_date_saves(page: Page):
    add_todo_with_due(page, "수정할 일정", "2099-01-01")
    row(page, "수정할 일정").get_by_role("button", name="수정").click()
    page.locator("input[type=date].title-edit").fill("2099-06-30")
    page.get_by_role("button", name="저장").click()
    expect(row(page, "수정할 일정").locator(".due")).to_have_text("마감: 2099-06-30")
