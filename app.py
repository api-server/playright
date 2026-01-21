import asyncio
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright
from urllib.parse import unquote, urlparse
import logging
import os
import base64

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Playwright URL Sniffer")

# Global set for ad domains (loaded from blockads.txt)
AD_DOMAINS = set()


def load_ad_blocklist():
    """
    Load ad domains from blockads.txt file
    Parses EasyList format: ||domain.com^ or ||domain.com^$options
    Handles both domains and IP addresses
    """
    global AD_DOMAINS
    blocklist_path = os.path.join(os.path.dirname(__file__), 'blockads.txt')
    
    if not os.path.exists(blocklist_path):
        logger.warning(f"blockads.txt not found at {blocklist_path}, ad blocking disabled")
        return
    
    try:
        with open(blocklist_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith('!'):
                    continue
                
                # Parse ||domain.com^ or ||domain.com^$options format
                if line.startswith('||') and '^' in line:
                    # Extract domain/IP between || and ^
                    domain = line[2:line.index('^')]
                    # Remove any options after ^ (e.g., $third-party)
                    domain = domain.split('$')[0].strip()
                    AD_DOMAINS.add(domain.lower())
        
        logger.info(f"Loaded {len(AD_DOMAINS)} ad domains from blocklist")
    except Exception as e:
        logger.error(f"Error loading blocklist: {e}")


def is_ad_request(url: str) -> bool:
    """
    Check if URL is from an ad network or blocked IP
    Uses efficient set lookup for fast performance
    Handles both domains and IP addresses
    """
    if not AD_DOMAINS:
        return False
    
    try:
        # Extract domain/IP from URL
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        
        # Remove port if present (e.g., domain.com:8080 -> domain.com)
        if ':' in netloc:
            netloc = netloc.split(':')[0]
        
        # Direct match (works for both domains and IPs)
        if netloc in AD_DOMAINS:
            return True
        
        # Check subdomain matches (e.g., ads.example.com matches example.com)
        # Skip for IP addresses (they don't have subdomains)
        if not netloc.replace('.', '').replace(':', '').isdigit():  # Not an IP
            parts = netloc.split('.')
            for i in range(len(parts)):
                subdomain = '.'.join(parts[i:])
                if subdomain in AD_DOMAINS:
                    return True
        
        return False
    except:
        return False

# Queue for managing requests
request_queue = asyncio.Queue()
processing = False


async def sniff_urls(target_url: str, filter_type: str = None):
    """
    Launch browser, navigate to URL, intercept requests, click to trigger streams
    Returns filtered URLs based on filter_type (m3u8, mpd, etc.)
    For filtered requests, exits early when target URL is found
    Collects ALL URLs regardless of response status (200, 403, 404, etc.)
    """
    collected_urls = []
    url_status_map = {}  # Track response status codes
    found_filtered_url = asyncio.Event()  # Signal when we find filtered URL
    
    async with async_playwright() as p:
        try:
            # Launch browser with minimal resources
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-software-rasterizer',
                    '--disable-extensions',
                    '--disable-background-networking',
                ]
            )
            
            # Create context with SSL error bypass and popup blocking
            context = await browser.new_context(
                ignore_https_errors=True,
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                # Block all popups and new windows
                viewport={'width': 1280, 'height': 720}
            )
            
            page = await context.new_page()
            
            # Block popup windows and new tabs
            context.on('page', lambda new_page: asyncio.create_task(new_page.close()))
            
            # Track response status codes
            async def handle_response(response):
                try:
                    url = response.url
                    status = response.status
                    url_status_map[url] = status
                    if status >= 400:
                        logger.debug(f"Response {status}: {url}")
                except Exception as e:
                    logger.debug(f"Error tracking response: {e}")
            
            page.on('response', handle_response)
            
            # Intercept all network requests
            async def handle_route(route):
                # Route object contains the request
                request = route.request
                url = request.url
                
                # Block ads and trackers
                if is_ad_request(url):
                    logger.debug(f"Blocked ad: {url}")
                    await route.abort()
                    return
                
                # Continue with non-ad requests
                await route.continue_()
                
                # Collect URLs based on filter
                if filter_type:
                    # Filter by specific type (e.g., m3u8, mpd)
                    # Match both extension (.m3u8) and query string (m3u8?)
                    filter_lower = filter_type.lower()
                    url_lower = url.lower()
                    
                    # Check if filter matches in URL path or extension
                    if (f'.{filter_lower}' in url_lower or 
                        f'{filter_lower}?' in url_lower or
                        f'/{filter_lower}/' in url_lower or
                        url_lower.endswith(f'.{filter_lower}') or
                        url_lower.endswith(filter_lower)):
                        collected_urls.append(url)
                        logger.info(f"Found {filter_type}: {url}")
                        # Signal that we found what we're looking for
                        found_filtered_url.set()
                else:
                    # Collect all URLs
                    collected_urls.append(url)
                    
            # Use route instead of on("request") to enable blocking
            await page.route("**/*", handle_route)
            
            # Block popups and overlays
            await page.add_init_script("""
                // Block popup overlays
                window.addEventListener('DOMContentLoaded', () => {
                    const styles = document.createElement('style');
                    styles.textContent = `
                        [class*="modal"], [class*="popup"], [class*="overlay"],
                        [id*="modal"], [id*="popup"], [id*="overlay"] {
                            display: none !important;
                        }
                    `;
                    document.head.appendChild(styles);
                });
            """)
            
            # Navigate to target URL
            logger.info(f"Navigating to: {target_url}")
            await page.goto(target_url, wait_until="networkidle", timeout=30000)
            
            # Wait a bit for initial load
            await asyncio.sleep(2)
            
            # Click in center of screen to trigger any video interactions
            try:
                viewport = page.viewport_size
                center_x = viewport['width'] // 2
                center_y = viewport['height'] // 2
                logger.info(f"Clicking center of screen at ({center_x}, {center_y})")
                await page.mouse.click(center_x, center_y)
                await asyncio.sleep(1)
            except Exception as e:
                logger.debug(f"Could not click center: {e}")
            
            # Try to find and click play buttons to trigger streams
            play_selectors = [
                'button[class*="play"]',
                'button[aria-label*="play" i]',
                'div[class*="play"]',
                '.play-button',
                '[data-testid*="play"]',
                'button.vjs-big-play-button',  # Video.js
                'video',  # Direct video element
            ]
            
            for selector in play_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        logger.info(f"Clicking element: {selector}")
                        await element.click()
                        await asyncio.sleep(3)  # Wait for stream to start
                        break
                except Exception as e:
                    logger.debug(f"Could not click {selector}: {e}")
                    continue
            
            # Wait 5 seconds for streams to fully load (or until we find filtered URL)
            if filter_type:
                # For filtered requests, wait max 5 seconds or until we find the URL
                try:
                    await asyncio.wait_for(found_filtered_url.wait(), timeout=20)
                    logger.info(f"Found filtered URL early, stopping browser")
                except asyncio.TimeoutError:
                    logger.info(f"5 second timeout reached, checking collected URLs")
            else:
                # For all URLs, wait the full 5 seconds
                await asyncio.sleep(5)
            
            # Final click in center to ensure player is activated (only if not found yet)
            if not filter_type or not found_filtered_url.is_set():
                try:
                    logger.info("Final center click to ensure player activation")
                    await page.mouse.click(center_x, center_y)
                    # Wait briefly for any final streams to load
                    if filter_type:
                        try:
                            await asyncio.wait_for(found_filtered_url.wait(), timeout=5)
                        except asyncio.TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(2)
                except Exception as e:
                    logger.debug(f"Could not perform final click: {e}")
            
            # Close browser
            await browser.close()
            logger.info(f"Collected {len(collected_urls)} URLs")
            
            # Log statistics about response codes
            error_urls = {url: status for url, status in url_status_map.items() if status >= 400}
            if error_urls:
                logger.info(f"Found {len(error_urls)} URLs with error status codes")
            
            return collected_urls
            
        except Exception as e:
            logger.error(f"Error during sniffing: {e}")
            if 'browser' in locals():
                await browser.close()
            raise e


async def process_queue():
    """
    Background task that processes requests from the queue one at a time
    """
    global processing
    while True:
        try:
            # Get next request from queue
            task = await request_queue.get()
            processing = True
            
            url = task['url']
            filter_type = task.get('filter_type')
            result_future = task['result']
            
            try:
                # Process the request
                logger.info(f"Processing: {url} (filter: {filter_type})")
                urls = await sniff_urls(url, filter_type)
                result_future.set_result(urls)
                
            except Exception as e:
                logger.error(f"Error processing request: {e}")
                result_future.set_exception(e)
            
            finally:
                request_queue.task_done()
                processing = False
                # Wait 2 seconds before processing next request
                logger.info("Waiting 2 seconds before next request...")
                await asyncio.sleep(2)
                
        except Exception as e:
            logger.error(f"Queue processing error: {e}")
            processing = False


@app.on_event("startup")
async def startup_event():
    """Start the background queue processor and load ad blocklist"""
    load_ad_blocklist()
    asyncio.create_task(process_queue())
    logger.info("Queue processor started")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "online",
        "service": "Playwright URL Sniffer",
        "queue_size": request_queue.qsize(),
        "processing": processing,
        "ad_domains_loaded": len(AD_DOMAINS)
    }


@app.get("/favicon.ico")
async def favicon():
    """Return 204 for favicon requests"""
    return JSONResponse(status_code=204, content=None)


@app.get("/api/{url:path}")
async def sniff_all_urls(url: str):
    """
    Sniff all URLs loaded by the target page
    Click play button to trigger streams
    Supports base64-encoded URLs (if URL doesn't start with http:// or https://)
    """
    # Check if URL is base64 encoded (doesn't start with http)
    if url.startswith(('http://', 'https://')):
        # Already a valid URL
        decoded_url = url
        logger.info(f"Using URL as-is: {decoded_url}")
    else:
        # Try to decode as base64
        try:
            decoded_bytes = base64.b64decode(url)
            decoded_url = decoded_bytes.decode('utf-8')
            logger.info(f"Decoded base64 URL: {decoded_url}")
        except Exception as e:
            # If decode fails, assume it's a plain URL without protocol
            logger.warning(f"Base64 decode failed ({e}), treating as plain URL")
            decoded_url = 'https://' + url
    
    # Create a future to hold the result
    result_future = asyncio.Future()
    
    # Add to queue
    await request_queue.put({
        'url': decoded_url,
        'filter_type': None,
        'result': result_future
    })
    
    queue_position = request_queue.qsize()
    logger.info(f"Request queued. Position: {queue_position}")
    
    # Wait for result
    try:
        urls = await asyncio.wait_for(result_future, timeout=60)
        return JSONResponse({
            "url": decoded_url,
            "total_urls": len(urls),
            "urls": urls
        })
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Request timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/{what}/{url:path}")
async def sniff_filtered_urls(what: str, url: str):
    """
    Sniff specific type of URLs (e.g., m3u8, mpd)
    Returns only URLs matching the filter
    Supports base64-encoded URLs (if URL doesn't start with http:// or https://)
    """
    # Check if URL is base64 encoded (doesn't start with http)
    if url.startswith(('http://', 'https://')):
        # Already a valid URL
        decoded_url = url
        logger.info(f"Using URL as-is: {decoded_url}")
    else:
        # Try to decode as base64
        try:
            decoded_bytes = base64.b64decode(url)
            decoded_url = decoded_bytes.decode('utf-8')
            logger.info(f"Decoded base64 URL: {decoded_url}")
        except Exception as e:
            # If decode fails, assume it's a plain URL without protocol
            logger.warning(f"Base64 decode failed ({e}), treating as plain URL")
            decoded_url = 'https://' + url
    
    # Create a future to hold the result
    result_future = asyncio.Future()
    
    # Add to queue
    await request_queue.put({
        'url': decoded_url,
        'filter_type': what,
        'result': result_future
    })
    
    queue_position = request_queue.qsize()
    logger.info(f"Request queued. Position: {queue_position}")
    
    # Wait for result
    try:
        urls = await asyncio.wait_for(result_future, timeout=60)
        return JSONResponse({
            "url": decoded_url,
            "filter": what,
            "total_urls": len(urls),
            "urls": urls
        })
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Request timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
