"""Shared browser ownership and bounded recovery for ordinary navigation."""
import asyncio
from pathlib import Path

from filelock import FileLock, Timeout
from playwright.async_api import Error, TimeoutError as NavigationTimeout, async_playwright


class BrowserBusyError(RuntimeError):
    pass


def check_profile_available(profile: Path):
    """Fast UI preflight; launch_profile still owns the authoritative lock."""
    profile.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(profile) + '.lock', timeout=0):
            pass
    except Timeout as exc:
        raise BrowserBusyError('该平台的登录或搜索窗口正在使用，请关闭该窗口后再试') from exc


class BrowserOwner:
    def __init__(self, driver, lock):
        self.driver, self.lock = driver, lock

    async def stop(self):
        try:
            await self.driver.stop()
        finally:
            self.lock.release()


async def launch_profile(profile: Path, **kwargs):
    profile.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(profile) + '.lock', timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise BrowserBusyError('该平台的登录或搜索窗口正在使用，请关闭该窗口后再试') from exc
    driver = None
    try:
        driver = await async_playwright().start()
        context = await driver.chromium.launch_persistent_context(str(profile), **kwargs)
        return BrowserOwner(driver, lock), context
    except BaseException:
        try:
            if driver is not None:
                await driver.stop()
        finally:
            lock.release()
        raise


async def navigate(page, url, *, timeout=45000):
    """Retry only transport failures once; never retry a closed browser or challenge."""
    for attempt in range(2):
        try:
            return await page.goto(url, wait_until='domcontentloaded', timeout=timeout)
        except Error as exc:
            transient = isinstance(exc, NavigationTimeout) or any(code in str(exc) for code in (
                'ERR_CONNECTION_RESET', 'ERR_CONNECTION_TIMED_OUT', 'ERR_NETWORK_CHANGED',
            ))
            if attempt or not transient:
                raise
            await asyncio.sleep(2)


async def close_browser(context, owner):
    try:
        if context is not None:
            await context.close()
    finally:
        if owner is not None:
            await owner.stop()
