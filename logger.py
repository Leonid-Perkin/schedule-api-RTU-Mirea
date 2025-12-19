import logging
import sys

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    # Отключаем лишние логи от playwright и других библиотек, если нужно
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)

logger = logging.getLogger("schedule-api")
