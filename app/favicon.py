import requests
from PIL import Image
from io import BytesIO
import os
import hashlib
from urllib.parse import urlparse
import asyncio
import favicon
from concurrent.futures import ProcessPoolExecutor

def hash_url(url):
    return hashlib.md5(url.encode('utf-8')).hexdigest()

# duplicated function in case this needs to change in future
def hash_profile_pic(filename):
    return hashlib.md5(filename.encode('utf-8')).hexdigest()


def get_domain_from_url(url):
    try:
        parsed_url = urlparse(url)
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
        return False


def resize_favicon(url):
    response = requests.get(url)
    if response.status_code == 200:
        img = Image.open(BytesIO(response.content))
        resized_img = img.resize((25, 25), Image.LANCZOS)
        hashed_url = hash_url(url)
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
    

async def get_favicon(url):
    if url is not None:
        try:
            domain = get_domain_from_url(url)
            if favicon_exists(domain):
                return favicon_exists
            icons = await get_favicon_with_timeout(domain)

            # return icons
            for i in range(5):
                try:
                    icon_link = icons[i].url
                    if icon_link:
                        response = requests.get(icon_link)
                        if response.status_code == 200:
                            return resize_favicon(icon_link)
                        else:
                            return None
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
    

