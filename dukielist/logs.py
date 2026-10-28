"""Log em arquivo: a única pista quando a sincronização falha em segundo plano.

A interface roda em tela cheia no terminal, então `print` e traceback no stderr
se perdem. As falhas de sincronização ficam registradas aqui.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_FILE = Path.home() / ".local" / "share" / "dukielist" / "dukielist.log"
MAX_BYTES = 512 * 1024
BACKUP_COUNT = 2

_ROOT_NAME = "dukielist"
_configured = False


def log_file(override: Path | None = None) -> Path:
    configured = os.environ.get("DUKIELIST_LOG")
    if override is not None:
        return override.expanduser()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_LOG_FILE


def setup_logging(override: Path | None = None, *, level: int = logging.INFO) -> Path | None:
    """Liga o log em arquivo uma única vez por processo.

    Devolve o caminho em uso, ou ``None`` quando não foi possível escrever —
    um log indisponível nunca deve impedir o aplicativo de abrir.
    """
    global _configured
    logger = logging.getLogger(_ROOT_NAME)
    if _configured:
        return getattr(logger, "_dukielist_path", None)

    path = log_file(override)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
    except OSError:
        _configured = True
        logger.addHandler(logging.NullHandler())
        return None

    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    logger.setLevel(level)
    logger.addHandler(handler)
    # A interface ocupa o terminal: nada de log subindo para o stderr do root.
    logger.propagate = False
    logger._dukielist_path = path  # type: ignore[attr-defined]
    _configured = True
    return path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
