from __future__ import annotations
from typing_extensions import Awaitable

import asyncio
import logging
import colorlog
import os
import signal
from tortoise import Tortoise

from . import env

getLogger = colorlog.getLogger
DEBUG = colorlog.DEBUG
INFO = colorlog.INFO
WARNING = colorlog.WARNING
ERROR = colorlog.ERROR
CRITICAL = colorlog.CRITICAL

logger_level_muted = colorlog.INFO if env.DEBUG else colorlog.WARNING
logger_level_shut_upped = colorlog.ERROR if env.DEBUG else colorlog.CRITICAL

getLogger('aiohttp_retry').setLevel(logger_level_muted)
getLogger('asyncio').setLevel(logger_level_muted)
getLogger('telethon').setLevel(logger_level_muted)
getLogger('aiosqlite').setLevel(logger_level_muted)
getLogger('tortoise').setLevel(logger_level_muted)
getLogger('asyncpg').setLevel(logger_level_muted)
getLogger('PIL').setLevel(logger_level_muted)
getLogger('matplotlib').setLevel(logger_level_muted)
getLogger('matplotlib.font_manager').setLevel(logger_level_shut_upped)

_logger = getLogger('RSStT.watchdog')


async def exit_handler(prerequisite: Awaitable = None):
    try:
        if prerequisite:
            try:
                await asyncio.wait_for(prerequisite, timeout=10)
            except asyncio.TimeoutError:
                _logger.critical('Failed to gracefully exit: prerequisite timed out')
        try:
            if env.bot and env.bot.is_connected():
                await env.bot.disconnect()
        finally:
            await Tortoise.close_connections()  # necessary, otherwise the connection will block the shutdown
    except Exception as e:
        _logger.critical('Failed to gracefully exit:', exc_info=e)
        os.kill(os.getpid(), signal.SIGTERM)
    exit(1)


def shutdown(prerequisite: Awaitable = None):
    if not env.loop.is_running():
        exit(1)
    asyncio.gather(env.loop.create_task(exit_handler(prerequisite)), return_exceptions=True)


class _Watchdog:
    def __init__(self, delay: int = 5 * 60):
        self._watchdog = env.loop.call_later(delay, self._exit_bot, delay)

    @staticmethod
    def _exit_bot(delay):
        msg = f'Never heard from the bot for {delay} seconds. Exiting...'
        _logger.critical(msg)
        coro = None
        if env.bot is not None:
            coro = env.bot.send_message(env.MANAGER, f'WATCHDOG: {msg}')
        shutdown(prerequisite=coro)

    def fine(self, delay: int = 15 * 60):
        self._watchdog.cancel()
        self._watchdog = env.loop.call_later(delay, self._exit_bot, delay)


# flit log from apscheduler.scheduler
class _APSCFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.count = 0
        self.watchdog = _Watchdog()

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg % record.args
        if 'skipped: maximum number of running instances reached' in msg:
            self.count += 1
            if self.count != 0 and self.count % 5 == 0:
                coro = env.bot.send_message(
                    env.MANAGER,
                    f'RSS monitor tasks have conflicted too many times ({self.count})!\n'
                    + ('Please store the log and restart.\n(sometimes it may be caused by too many subscriptions)'
                       if self.count < 15 else
                       'Now the bot will restart.')
                    + '\n\n' + msg
                )
                if self.count >= 15:
                    _logger.critical(f'RSS monitor tasks have conflicted too many times ({self.count})! Exiting...')
                    shutdown(prerequisite=coro)
                else:
                    env.loop.create_task(coro)
            return True
        if ' executed successfully' in msg:
            self.count = 0
            self.watchdog.fine()
            return False
        if 'Running job "run_monitor_task' in msg:
            return False
        return True


apsc_filter = _APSCFilter()
getLogger('apscheduler').setLevel(colorlog.WARNING)
getLogger('apscheduler.executors.default').setLevel(colorlog.INFO)
getLogger('apscheduler.scheduler').addFilter(apsc_filter)
getLogger('apscheduler.executors.default').addFilter(apsc_filter)


class AiohttpAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg % record.args
        if record.levelno <= logging.INFO and 'Mozilla' not in msg:
            return False
        return True


aiohttp_access_filter = AiohttpAccessFilter()
getLogger('aiohttp.access').addFilter(aiohttp_access_filter)
