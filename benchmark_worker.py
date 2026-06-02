"""Benchmark: measure RAM of one Playwright Chromium worker (non-headless) using about:blank."""
import asyncio
import psutil
from playwright.async_api import async_playwright
from playwright_stealth import Stealth


async def measure():
    vm_before = psutil.virtual_memory()
    mem_used_before = vm_before.used / 1024 / 1024

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=False,
        args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        locale="ru-RU",
    )
    page = await ctx.new_page()
    stealth = Stealth()
    await stealth.apply_stealth_async(page)

    # Load a lightweight page to get realistic baseline (not the blocked site)
    await page.goto("about:blank")
    await asyncio.sleep(2)

    # Snapshot after browser is idle with one tab
    vm_mid = psutil.virtual_memory()
    mem_mid = vm_mid.used / 1024 / 1024
    idle_cost = mem_mid - mem_used_before

    # Now simulate loading a typical HTML-heavy page (Wikipedia as proxy)
    print("Loading a typical page to measure real memory + bandwidth...")
    net_before = psutil.net_io_counters()
    await page.goto("https://ru.wikipedia.org/wiki/Арбитражный_суд", wait_until="domcontentloaded", timeout=20000)
    await asyncio.sleep(3)

    vm_after = psutil.virtual_memory()
    net_after = psutil.net_io_counters()
    mem_after = vm_after.used / 1024 / 1024
    loaded_cost = mem_after - mem_used_before

    bytes_sent = (net_after.bytes_sent - net_before.bytes_sent) / 1024 / 1024
    bytes_recv = (net_after.bytes_recv - net_before.bytes_recv) / 1024 / 1024

    avail_mb = vm_after.available / 1024 / 1024
    total_mb = vm_after.total / 1024 / 1024

    print(f"\n{'='*55}")
    print(f" SYSTEM: i7-12700F, {total_mb:.0f} MB RAM, {avail_mb:.0f} MB free")
    print(f"{'='*55}")
    print(f" RAM (idle browser, blank page):  ~{idle_cost:.0f} MB")
    print(f" RAM (loaded page + rendering):   ~{loaded_cost:.0f} MB")
    print(f" Network for 1 page load:")
    print(f"   Sent:     {bytes_sent:.2f} MB")
    print(f"   Received: {bytes_recv:.2f} MB")
    print(f"{'='*55}")

    # kad.arbitr.ru pages are heavier than Wikipedia (JS-heavy SPA)
    # Add 30% overhead estimate
    per_worker = max(loaded_cost * 1.3, 350)
    page_size_mb = max(bytes_recv * 1.5, 1.0)  # kad pages are JS-heavier

    print(f"\n ESTIMATES FOR kad.arbitr.ru (non-headless):")
    print(f"   RAM per worker:   ~{per_worker:.0f} MB (with 30% SPA overhead)")
    print(f"   Per page load:    ~{page_size_mb:.1f} MB download")
    print(f"")

    # Worker capacity
    reserve_mb = 4096  # keep 4 GB for OS + dashboard + other apps
    safe_avail = avail_mb - reserve_mb
    max_w = max(1, int(safe_avail / per_worker))

    print(f" MAX WORKERS (keeping {reserve_mb/1024:.0f} GB for OS):")
    print(f"   {safe_avail:.0f} MB / {per_worker:.0f} MB = {max_w} workers")
    print(f"")

    # Bandwidth estimates
    # Per judge: ~25 case list pages (1 API call each) + 25 case detail pages = ~50 loads
    # With delays averaging 4s between actions, 1 judge takes ~4 minutes
    # So ~15 judges/hour/worker, ~750 page loads/hour/worker
    loads_per_hour_per_worker = 50 * 15  # conservative
    bw_hour_1w = page_size_mb * loads_per_hour_per_worker
    bw_hour_all = bw_hour_1w * max_w

    # More realistic: with your delays (~4-6s per page), ~12 loads/min = 720/h
    realistic_loads = 600  # ~10 loads/min accounting for delays
    real_bw_1w = page_size_mb * realistic_loads
    real_bw_all = real_bw_1w * max_w

    print(f" BANDWIDTH per worker:")
    print(f"   ~{real_bw_1w:.0f} MB/hour ({real_bw_1w/1024:.1f} GB/hour)")
    print(f"")
    print(f" BANDWIDTH at {max_w} workers:")
    print(f"   ~{real_bw_all:.0f} MB/hour ({real_bw_all/1024:.1f} GB/hour)")
    print(f"   ~{real_bw_all * 24 / 1024:.0f} GB/day (24h continuous)")
    print(f"{'='*55}")

    await browser.close()
    await p.stop()


if __name__ == "__main__":
    asyncio.run(measure())
