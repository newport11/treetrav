import asyncio
import hashlib
import os
import urllib.parse
from concurrent.futures import ProcessPoolExecutor
from io import BytesIO

import favicon
import requests
from PIL import Image

import logging

current_dir = os.path.dirname(os.path.abspath(__file__))
log_file = os.path.join(current_dir, 'app.log')


logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename=log_file,
    filemode='a'
)
logger = logging.getLogger(__name__)


def hash_url(url):
    return hashlib.md5(url.encode('utf-8')).hexdigest()

# duplicated function in case this needs to change in future
def hash_profile_pic(filename):
    return hashlib.md5(filename.encode('utf-8')).hexdigest()


def get_domain_from_url(url):
    try:
        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.netloc:
            return f"{parsed_url.scheme}://{parsed_url.netloc}"
        else:
            return None
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        return None
    
def favicon_exists(url):
    directory_path="app/static/favicons/"
    hashed_url = hash_url(url)
    filename=f"{hashed_url}.png"
    file_path = os.path.join(directory_path, filename)
    if os.path.exists(file_path):
        return f"{hashed_url}.png"
    else:
        return None


def resize_favicon(url, domain):
    response = requests.get(url)
    if response.status_code == 200:
        img = Image.open(BytesIO(response.content))
        resized_img = img.resize((25, 25), Image.LANCZOS)
        hashed_url = hash_url(domain)
        resized_img.save(f"app/static/favicons/{hashed_url}.png")
        return f"{hashed_url}.png"
    else:
        print(f"Error: Unable to fetch favicon from {url}")
        return None



async def get_favicon_with_timeout(domain, timeout = 8):
    loop = asyncio.get_event_loop()
    loop.set_default_executor(ProcessPoolExecutor())
    
    try:
        # Use asyncio.wait_for to add a timeout to the execution
        icons = await asyncio.wait_for(loop.run_in_executor(None, favicon.get, domain), timeout)
        return icons
    except asyncio.CancelledError:
        print("cancelled")
        return None
    except asyncio.TimeoutError:
        print(f"Execution of favicon.get for {domain} timed out.")
        return None
    

async def get_favicon_2(url):
    if url is not None:
        url = urllib.parse.unquote(url)
        try:
            domain = get_domain_from_url(url)
            existing_favicon = favicon_exists(domain)
            if existing_favicon:
                return existing_favicon
            icons = await get_favicon_with_timeout(domain)
            
            # return icons
            for i in range(5):
                try:
                    icon_link = icons[i].url
                    if icon_link:
                        response = requests.get(icon_link)

                        if response.status_code == 200:
                            return resize_favicon(icon_link, domain)
                        else:
                            continue
                except IndexError:
                    break
                except Exception as e:
                    print(f"An error occurred: {str(e)}")
                    continue
            return None
        except KeyboardInterrupt:
            print("KeyboardInterrupt: Stopping the program")
            return None
        except:
            return None
    else:
        return None
    

async def get_favicon(url):
    if url is not None:
        url = urllib.parse.unquote(url)
        logger.debug(f"Unquoted URL: {url}")
        try:
            domain = get_domain_from_url(url)
            logger.debug(f"Extracted domain: {domain}")

            existing_favicon = favicon_exists(domain)
            if existing_favicon:
                logger.info(f"Existing favicon found for domain: {domain}")
                return existing_favicon

            icons = await get_favicon_with_timeout(domain)
            logger.debug(f"Icons retrieved: {icons}")

            for i in range(5):
                try:
                    icon_link = icons[i].url
                    logger.debug(f"Trying icon link: {icon_link}")

                    if icon_link:
                        response = requests.get(icon_link)
                        logger.debug(f"Response for {icon_link}: {response}")

                        if response.status_code == 200:
                            logger.info(f"Successful 200 response for {icon_link}")
                            return resize_favicon(icon_link, domain)
                        else:
                            logger.warning(f"Non-200 response for {icon_link}: {response.status_code}")
                            continue
                except IndexError:
                    logger.warning(f"IndexError: No more icons to try after {i} attempts")
                    break
                except Exception as e:
                    logger.error(f"An error occurred while processing icon link {icon_link}: {str(e)}", exc_info=True)
                    continue
            logger.info(f"No suitable favicon found for domain: {domain}")
            return None
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt: Stopping the program")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred: {str(e)}", exc_info=True)
            return None
    else:
        logger.warning("No URL provided")
        return None
