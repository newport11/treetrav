import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
import os
import hashlib
from urllib.parse import urlparse
import favicon
import urllib.parse


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
    try:
        response = requests.get(url)
        if response.status_code == 200:
            img = Image.open(BytesIO(response.content))
            resized_img = img.resize((25, 25), Image.LANCZOS)
            hashed_url = hash_url(url)
            resized_img.save(f"app/static/favicons/{hashed_url}.png")
            return f"{hashed_url}.png"
        else:
            print(f"Error: Unable to fetch favicon from {url}")
    except Exception as e:
        print(f"An error occurred: {str(e)}")


def get_favicon(domain):
    if domain is not None:
        domain = urllib.parse.unquote(domain)
        if favicon_exists(domain):
            return favicon_exists
        try:
            icons = favicon.get(domain)
            icon_link = icons[0].url
        except:
            icon_link = None
        if icon_link is None:
            if 'http' not in domain:
                domain = 'http://' + domain
            try:
                page = requests.get(domain)
                soup = BeautifulSoup(page.text, "lxml")
                icon_link = soup.find("link", rel="shortcut icon")
                if icon_link is None:
                    icon_link = soup.find("link", rel="icon")
                if icon_link is None:
                    icon_link = get_domain_from_url(domain) + 'favicon.ico'
                else:
                    icon_link = icon_link["href"]
            except:
                return None
        try:
            response = requests.get(icon_link)
            if response.status_code == 200:
                return resize_favicon(icon_link)
            else:
                return None
        except:
            return None
    else:
        return None