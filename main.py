import asyncio
import os
import json
import time
import re
from urllib.parse import quote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

CACHE_DIR = "schedule_cache"
CACHE_TTL = 86400

def is_cache_valid(cache_filename: str) -> bool:
    if not os.path.exists(cache_filename):
        return False
    return (time.time() - os.path.getmtime(cache_filename)) < CACHE_TTL


def load_from_cache(group: str, date: str) -> dict | None:
    cache_filename = os.path.join(CACHE_DIR, f"{group}_{date}.json")
    if is_cache_valid(cache_filename):
        with open(cache_filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_to_cache(group: str, date: str, schedule: list):
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    cache_filename = os.path.join(CACHE_DIR, f"{group}_{date}.json")
    with open(cache_filename, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)


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
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(1)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.5)
            schedule_blocks = await page.query_selector_all('div.TimeLine_fullcalendarText__fm4tW')
            if not schedule_blocks:
                await browser.close()
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

        except Exception as e:
            print(f"Ошибка при парсинге: {e}")
            schedule = []
        finally:
            await browser.close()

    save_to_cache(group, date, schedule)
    return schedule


from datetime import datetime


async def main():
    group_name = "БАСО-03-24"
    today_date = datetime.now().strftime("%Y-%m-%d")

    print(f"--- Загрузка расписания для {group_name} на {today_date} ---")

    schedule = await get_day_schedule(group_name, today_date)

    if not schedule:
        print("Пар не найдено или произошла ошибка парсинга.")
        return

    for i, item in enumerate(schedule, 1):
        print(f"\nПара №{i}")
        print(f"⏰ Время: {item['time']}")
        print(f"📖 Тип: {item['type']}")
        print(f"📚 Предмет: {item['subject']}")
        print(f"🏫 Аудитория: {item['room']}")
        print(f"👨‍🏫 Преподаватель: {item['teacher']}")
        print(f"👥 Группы: {', '.join(item['groups'])}")


if __name__ == "__main__":
    asyncio.run(main())