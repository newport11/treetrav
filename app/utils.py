import copy
import logging
from urllib.parse import urlparse
from newspaper import Article

import requests
from bs4 import BeautifulSoup

from app import db
from app.models import Post, User

# PATH UTILS ---------------------------------------


def is_subpath(subpath, path):
    # Split the paths into their components
    if not subpath or not path:
        return False
    subpath_components = subpath.split("/")
    path_components = path.split("/")

    # Check if subpath is a valid subpath of path
    if len(subpath_components) > len(path_components):
        return False

    for i in range(len(subpath_components)):
        if subpath_components[i] != path_components[i]:
            return False
    return True


def validate_folder_path(username, folder_path):
    if folder_path.strip() != "/":
        user = User.query.filter_by(username=username).first()
        folder_path = folder_path.strip().strip("/")
        posts = user.posts.all()
        filtered_posts = filter(
            lambda post: is_subpath(folder_path, post.folder_link), posts
        )
        filtered_posts_list = list(filtered_posts)
        if not filtered_posts_list:
            return False
        return True
    else:
        return True


def rename_folder_util(username, folder_path, folder_name):
    user = User.query.filter_by(username=username).first()
    posts = user.posts.all()
    filtered_posts = filter(
        lambda post: is_subpath(folder_path, post.folder_link), posts
    )
    filtered_posts_list = list(filtered_posts)

    tmp_path = folder_path.rsplit("/", 1)
    if len(tmp_path) == 2:
        new_folder_path = tmp_path[0] + "/" + folder_name
    else:
        new_folder_path = folder_name
    for post in filtered_posts_list:
        if post.folder_link.startswith(folder_path):
            post.folder_link = new_folder_path + post.folder_link[len(folder_path) :]
    db.session.commit()


def copy_folder_util(current_user, origin_path, dest_path):
    user = User.query.filter_by(username=current_user.username).first()
    posts = user.posts.all()
    post = list(posts)
    if origin_path == "/":
        for post in posts:
            new_post = Post(
                link=post.link,
                body=post.body,
                description=post.description,
                folder_link=post.folder_link,
                author=current_user,
                favicon_file_name=post.favicon_file_name,
            )
            if dest_path != "/":
                if new_post.folder_link:
                    new_post.folder_link = dest_path + "/" + new_post.folder_link
                else:
                    new_post.folder_link = dest_path
            db.session.add(new_post)
    else:
        filtered_posts = filter(
            lambda post: is_subpath(origin_path, post.folder_link), posts
        )
        filtered_posts_list = list(filtered_posts)
        for post in filtered_posts_list:
            new_post = Post(
                link=post.link,
                body=post.body,
                description=post.description,
                folder_link=post.folder_link,
                author=current_user,
                favicon_file_name=post.favicon_file_name,
            )
            if new_post.folder_link.startswith(origin_path):
                if dest_path != "/":
                    new_post.folder_link = (
                        dest_path + new_post.folder_link[len(origin_path) :]
                    )
                else:
                    new_post.folder_link = "/"
            db.session.add(new_post)
    db.session.commit()


def move_folder_util(current_user, origin_path, dest_path):
    if origin_path == "/":
        return
    user = User.query.filter_by(username=current_user.username).first()
    posts = user.posts.all()
    filtered_posts = filter(
        lambda post: is_subpath(origin_path, post.folder_link), posts
    )
    filtered_posts_list = list(filtered_posts)
    for post in filtered_posts_list:
        if post.folder_link.startswith(origin_path):
            if dest_path != "/":
                post.folder_link = dest_path + post.folder_link[len(origin_path) :]
            else:
                post.folder_link = "/"
    db.session.commit()


def get_webpage_title(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Check for <title> tag
        if soup.title and soup.title.string:
            return soup.title.string.strip()

        # Check for OpenGraph title
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()

        # Check for Twitter card title
        twitter_title = soup.find("meta", attrs={"name": "twitter:title"})
        if twitter_title and twitter_title.get("content"):
            return twitter_title["content"].strip()

        # Check for the first <h1> tag
        h1 = soup.find("h1")
        if h1 and h1.string:
            return h1.string.strip()
        
        # Check for newspaper
        article = Article(url)
        article.download()
        article.parse()
        if article.title:
            return article.title

        # Use the domain name as a last resort
        domain = urlparse(url).netloc
        return domain.replace("www.", "").capitalize()

    except Exception as e:
        logging.error(f"Error fetching webpage title: {str(e)}")
        return None
