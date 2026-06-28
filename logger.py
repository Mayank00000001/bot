"""logger.py — Simple logging setup."""
import logging, sys
from pathlib import Path

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s — %(message)s", "%Y-%m-%d %H:%M:%S")
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        logger.addHandler(ch)
        Path("logs").mkdir(exist_ok=True)
        fh = logging.FileHandler("logs/bot.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger
