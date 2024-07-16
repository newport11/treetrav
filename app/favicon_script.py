import hashlib
import urllib.parse
from io import BytesIO

from PIL import Image


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
    
    
if __name__ == "__main__":
    url = "https://pcpartpicker.com/guide/fWv6Mp/enthusiast-intel-gamingstreaming-build"
    pic_name = "pcpick.png"
    url_pic_dict = {
                    }
    for pic_name, url in url_pic_dict.items():

        domain = get_domain_from_url(url)

        hashed_domain = hash_url(domain)

        print(hashed_domain)

        img = Image.open(f"app/static/favicons/unsized/{pic_name}")
        resized_img = img.resize((25, 25), Image.LANCZOS)
        resized_img.save(f"app/static/favicons/{hashed_domain}.png")