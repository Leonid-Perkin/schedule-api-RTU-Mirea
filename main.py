import asyncio
import os
import json
import time
import re
from contextlib import asynccontextmanager
from urllib.parse import quote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Browser

from fastapi import FastAPI, HTTPException, Query
from datetime import datetime, timedelta

from config import settings
from logger import setup_logging, logger

setup_logging()

class PlaywrightManager:
    def __init__(self):
        self.playwright = None
        self.browser = None

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=settings.BROWSER_HEADLESS,
            args=['--disable-blink-features=AutomationControlled']
        )
        logger.info("Браузер Playwright успешно запущен")

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("Браузер Playwright остановлен")

    async def get_new_page(self):
        if not self.browser:
            await self.start()
        context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
        )
        return await context.new_page()

pw_manager = PlaywrightManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await pw_manager.start()
    yield
    await pw_manager.stop()

app = FastAPI(title=settings.APP_TITLE, lifespan=lifespan)

CACHE_DIR = settings.CACHE_DIR
CACHE_TTL = settings.CACHE_TTL

def is_cache_valid(cache_filename: str) -> bool:
    if not os.path.exists(cache_filename):
        return False
    return (time.time() - os.path.getmtime(cache_filename)) < CACHE_TTL


def load_from_cache(group: str, date: str) -> dict | None:
    safe_group = re.sub(r'[^A-Za-z0-9-А-Яа-яёЁ]', '_', group)
    cache_filename = os.path.join(CACHE_DIR, f"{safe_group}_{date}.json")
    if is_cache_valid(cache_filename):
        try:
            with open(cache_filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    return None


def save_to_cache(group: str, date: str, schedule: list):
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    safe_group = re.sub(r'[^A-Za-z0-9-А-Яа-яёЁ]', '_', group)
    cache_filename = os.path.join(CACHE_DIR, f"{safe_group}_{date}.json")
    try:
        with open(cache_filename, "w", encoding="utf-8") as f:
            json.dump(schedule, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.error(f"Ошибка сохранения кэша: {e}")


def parse_time_to_minutes(time_str: str) -> int:
    try:
        start_time = time_str.split(" - ")[0]
        hours, minutes = map(int, start_time.split(":"))
        return hours * 60 + minutes
    except (ValueError, IndexError):
        return float('inf')

async def get_day_schedule(group: str, date: str) -> list:
    cached_schedule = load_from_cache(group, date)
    if cached_schedule is not None:
        return cached_schedule
    
    encoded_group = quote(group)
    url = f"https://schedule-of.mirea.ru/?scheduleTitle={encoded_group}&date={date}"
    
    page = await pw_manager.get_new_page()
    try:
        try:
            await page.goto(url, wait_until="networkidle", timeout=settings.PLAYWRIGHT_TIMEOUT)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(1)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.5)
            schedule_blocks = await page.query_selector_all('div.TimeLine_fullcalendarText__fm4tW')
            if not schedule_blocks:
                return []
            
            schedule = []
            period = "Не указан"
            for block in schedule_blocks:
                time_subject_elem = await block.query_selector('strong.TimeLine_eventTitle__oq7tU')
                time_subject_text = (
                    await time_subject_elem.inner_text()).strip() if time_subject_elem else "Нет данных"
                if "неделя" in time_subject_text.lower() and not any(char in time_subject_text for char in [":", "-"]):
                    period = time_subject_text
                    continue
                elif "сессия" in time_subject_text.lower() and not any(
                        char in time_subject_text for char in [":", "-"]):
                    period = "Сессия"
                    continue
                time_match = re.match(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*(.+)", time_subject_text)
                if time_match:
                    start_time, end_time, subject_raw = time_match.groups()
                    current_time = f"{start_time} - {end_time}"
                    subject_raw = subject_raw.strip()
                else:
                    parts = time_subject_text.split(" ", 1)
                    if len(parts) == 2 and "-" in parts[0]:
                        current_time = parts[0].replace(" ", "")
                        subject_raw = parts[1].strip()
                    else:
                        current_time = "Нет времени"
                        subject_raw = time_subject_text
                lesson_type = "Не указан"
                subject_name = subject_raw
                if "|" in subject_raw:
                    parts = [p.strip() for p in subject_raw.split("|") if p.strip()]
                    if len(parts) >= 2:
                        lesson_type = parts[0]
                        subject_name = parts[1]
                    elif len(parts) == 1:
                        subject_name = parts[0]
                subject_name = subject_name.strip()
                details_block = await block.query_selector('div[style="white-space: nowrap;"]')
                room = await details_block.query_selector('strong') if details_block else None
                room = (await room.inner_text()).strip() if room else "Нет данных"
                await block.hover()
                await asyncio.sleep(0.3)
                try:
                    dialog = await page.wait_for_selector('div[role="dialog"]', timeout=3000)
                    extra_info = (await dialog.inner_text()).strip().split("\n") if dialog else []
                    await page.mouse.click(0, 0)
                except PlaywrightTimeoutError:
                    extra_info = []

                teacher = "Нет данных"
                groups = ["Нет данных о группах"]

                if extra_info:
                    for line in extra_info:
                        if "Преподаватель:" in line:
                            teacher = line.replace("Преподаватель:", "").strip()
                            break

                    groups_section = False
                    groups = []
                    for line in extra_info:
                        if "Группы:" in line:
                            groups_section = True
                            continue
                        if groups_section and line.strip():
                            if "БАСО-" in line:
                                groups.append(line.strip())
                        elif groups_section and not line.strip():
                            break
                    if not groups:
                        groups = ["Нет данных о группах"]

                schedule.append({
                    "period": period,
                    "time": current_time,
                    "type": lesson_type,
                    "subject": subject_name,
                    "room": room,
                    "teacher": teacher,
                    "groups": groups
                })

            schedule.sort(key=lambda x: parse_time_to_minutes(x["time"]))
            save_to_cache(group, date, schedule)
            return schedule

        except Exception as e:
            logger.error(f"Ошибка при парсинге {group} на {date}: {e}")
            return []
    finally:
        await page.close()
        await page.context.close()
GROUP_REGEX = re.compile(r"^[А-Я]{4}-\d{2}-\d{2}$")

def validate_group(group: str):
    if not GROUP_REGEX.match(group):
        if not (3 <= len(group) <= 15 and re.match(r"^[А-ЯЁа-яёA-Za-z0-9-]+$", group)):
            raise HTTPException(
                status_code=400, 
                detail="Неверный формат группы. Пример корректного формата: БАСО-03-24."
            )
def validate_date_range(date_str: str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        now = datetime.now()
        if dt < now - timedelta(days=365*2) or dt > now + timedelta(days=365*2):
            raise HTTPException(
                status_code=400, 
                detail="Дата вне допустимого диапазона (должна быть в пределах 2 лет от текущей даты)."
            )
        return dt
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте ГГГГ-ММ-ДД.")

@app.get("/schedule")
async def get_schedule(
    group: str = Query(..., description="Название группы, например БАСО-03-24"),
    date: str = Query(None, description="Дата в формате ГГГГ-ММ-ДД. По умолчанию сегодня.")
):
    validate_group(group)
    
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    else:
        validate_date_range(date)

    schedule = await get_day_schedule(group, date)
    
    return {
        "group": group,
        "date": date,
        "schedule": schedule
    }


@app.get("/schedule/week")
async def get_weekly_schedule(
    group: str = Query(..., description="Название группы, например БАСО-03-24"),
    date: str = Query(None, description="Дата в рамках недели в формате ГГГГ-ММ-ДД. По умолчанию сегодня.")
):
    validate_group(group)
    if not date:
        date_dt = datetime.now()
    else:
        date_dt = validate_date_range(date)
    monday = date_dt - timedelta(days=date_dt.weekday())
    week_dates = [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    tasks = [get_day_schedule(group, d) for d in week_dates]
    results = await asyncio.gather(*tasks)
    
    weekly_schedule = []
    for d, s in zip(week_dates, results):
        weekly_schedule.append({
            "date": d,
            "schedule": s
        })

    return {
        "group": group,
        "week_start": monday.strftime("%Y-%m-%d"),
        "schedules": weekly_schedule
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)