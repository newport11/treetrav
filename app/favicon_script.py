from PIL import Image
from io import BytesIO

import hashlib

import urllib.parse


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
    url_pic_dict = {"bestbuy.png" : "https://www.bestbuy.com/site/corsair-icue-4000d-rgb-airflow-atx-mid-tower-case-black/6530795.p?acampID=0&cmp=RMX&intl=nosplash&irclickid=QBQU7iyrgxyPU2uWIHUjI0EbUkFQuTy9rSwWU00&irgwc=1&loc=PCPartPicker&mpid=79301&ref=198&refdomain=pcpartpicker.com&skuId=6530795",
                    "newegg.png" : "https://www.newegg.com/mushkin-enhanced-2tb-vortex-lx/p/N82E16820992013?Item=N82E16820992013&nm_mc=AFC-RAN-COM&cm_mmc=afc-ran-com-_-PCPartPicker&utm_medium=affiliate&utm_campaign=afc-ran-com-_-PCPartPicker&utm_source=afc-PCPartPicker&AFFID=2558510&AFFNAME=PCPartPicker&ACRID=1&ASID=https://pcpartpicker.com/&ranMID=44583&ranEAID=2558510&ranSiteID=8BacdVP0GFs-ROv47cvQmCkruegdaGLxog",
                    "bhp.png" : "https://www.bhphotovideo.com/",
                    "adorama.png" : "https://www.adorama.com/dcrak4bkmng2.html?sterm=1pmTuKyrgxyPTJ714M3kaW7iUkFQuWQZrSwWU00&utm_source=rflaid912925&utm_medium=affiliate",
                    "homedepot.png" : "https://www.homedepot.ca/en/home.html",
                    "rh.png" : "https://rh.com/ca/en/catalog/product/product.jsp?productId=prod29740559&layout=square",
                    "etsy.png" : "https://www.etsy.com/ca/listing/698614034/1980s-new-york-city-cityscape-with-twin?click_key=0f548562b49e181896ee6274792ba500ba18ac84%3A698614034&click_sum=0c8e6ebf&ref=hp_rv-1&sts=1"
                    }
    for pic_name, url in url_pic_dict.items():

        domain = get_domain_from_url(url)

        hashed_domain = hash_url(domain)

        print(hashed_domain)

        img = Image.open(f"app/static/favicons/unsized/{pic_name}")
        resized_img = img.resize((25, 25), Image.LANCZOS)
        resized_img.save(f"app/static/favicons/{hashed_domain}.png")