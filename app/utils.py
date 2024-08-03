import logging
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from newspaper import Article
from PIL import Image

from app import db
from app.models import Post, User

# PATH UTILS ---------------------------------------


def is_subpath(subpath: str, path: str) -> bool:
    """
    Check if one path is a subpath of another.

    This function determines whether 'subpath' is a valid subpath of 'path'.
    It splits both paths into components and compares them sequentially.

    Args:
        subpath (str): The potential subpath to check.
        path (str): The main path to check against.

    Returns:
        bool: True if 'subpath' is a valid subpath of 'path', False otherwise.

    Examples:
        >>> is_subpath("/a/b", "/a/b/c")
        True
        >>> is_subpath("/a/b/d", "/a/b/c")
        False
        >>> is_subpath("/a/b/c", "/a/b")
        False
    """
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


def validate_folder_path(username: str, folder_path: str) -> bool:
    """
    Validate if a given folder path is valid for a user.

    This function checks if the provided folder path is valid for the specified user.
    It considers the root path ("/") as always valid. For other paths, it checks
    if there are any posts associated with the user that have the given folder path
    as a subpath of their folder link.

    Args:
        username (str): The username of the user to validate the folder path for.
        folder_path (str): The folder path to validate.

    Returns:
        bool: True if the folder path is valid for the user, False otherwise.

    Examples:
        >>> validate_folder_path("user1", "/")
        True
        >>> validate_folder_path("user1", "/valid/path")  # Assuming user has posts in this path
        True
        >>> validate_folder_path("user1", "/invalid/path")  # Assuming user has no posts in this path
        False
    """
    if folder_path.strip() != "/":
        user = User.query.filter_by(username=username).first()
        if user is None:
            return False

        folder_path = folder_path.strip().strip("/")
        posts: List[Post] = user.posts.all()
        filtered_posts = filter(
            lambda post: is_subpath(folder_path, post.folder_link), posts
        )
        filtered_posts_list = list(filtered_posts)
        return bool(filtered_posts_list)
    else:
        return True


def rename_folder_util(username: str, folder_path: str, folder_name: str) -> None:
    """
    Rename a folder for a user and update all associated post folder links.

    This function renames a folder for the specified user by updating the folder links
    of all posts that are within the given folder path. It constructs a new folder path
    based on the provided folder name and updates all relevant post folder links.

    Args:
        username (str): The username of the user whose folder is being renamed.
        folder_path (str): The current path of the folder to be renamed.
        folder_name (str): The new name for the folder.

    Returns:
        None

    Raises:
        ValueError: If the user is not found.

    Example:
        >>> rename_folder_util("user1", "/old/folder", "new_folder")
        # This will rename "/old/folder" to "/old/new_folder" and update all post links accordingly.
    """
    user = User.query.filter_by(username=username).first()
    if user is None:
        raise ValueError(f"User '{username}' not found")

    posts: List[Post] = user.posts.all()
    filtered_posts = filter(
        lambda post: is_subpath(folder_path, post.folder_link), posts
    )
    filtered_posts_list = list(filtered_posts)

    tmp_path = folder_path.rsplit("/", 1)
    if len(tmp_path) == 2:
        new_folder_path = f"{tmp_path[0]}/{folder_name}"
    else:
        new_folder_path = folder_name

    for post in filtered_posts_list:
        if post.folder_link.startswith(folder_path):
            post.folder_link = new_folder_path + post.folder_link[len(folder_path) :]

    db.session.commit()


def copy_folder_util(current_user: User, origin_path: str, dest_path: str) -> None:
    """
    Copy a folder and its contents for a user.

    This function copies a folder and all its contents (posts) from one location
    to another for the specified user. It handles both root folder ("/") and
    subfolder copying.

    Args:
        current_user (User): The current authenticated user.
        origin_path (str): The path of the folder to be copied.
        dest_path (str): The destination path where the folder will be copied to.

    Returns:
        None

    Raises:
        ValueError: If the user is not found.

    Example:
        >>> copy_folder_util(current_user, "/old_folder", "/new_folder")
        # This will copy the contents of "/old_folder" to "/new_folder"
    """
    user = User.query.filter_by(username=current_user.username).first()
    if user is None:
        raise ValueError(f"User '{current_user.username}' not found")

    posts: List[Post] = user.posts.all()

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
                new_post.folder_link = (
                    f"{dest_path}/{new_post.folder_link}"
                    if new_post.folder_link
                    else dest_path
                )
            db.session.add(new_post)
    else:
        filtered_posts = filter(
            lambda post: is_subpath(origin_path, post.folder_link), posts
        )
        for post in filtered_posts:
            new_post = Post(
                link=post.link,
                body=post.body,
                description=post.description,
                folder_link=post.folder_link,
                author=current_user,
                favicon_file_name=post.favicon_file_name,
            )
            if new_post.folder_link.startswith(origin_path):
                new_post.folder_link = (
                    f"{dest_path}{new_post.folder_link[len(origin_path):]}"
                    if dest_path != "/"
                    else "/"
                )
            db.session.add(new_post)

    db.session.commit()


def move_folder_util(current_user: User, origin_path: str, dest_path: str) -> None:
    """
    Move a folder and its contents for a user.

    This function moves a folder and all its contents (posts) from one location
    to another for the specified user. It updates the folder links of all posts
    within the origin folder to reflect the new destination path.

    Args:
        current_user (User): The current authenticated user.
        origin_path (str): The current path of the folder to be moved.
        dest_path (str): The destination path where the folder will be moved to.

    Returns:
        None

    Raises:
        ValueError: If the user is not found or if attempting to move the root folder.

    Example:
        >>> move_folder_util(current_user, "/old_location", "/new_location")
        # This will move the contents of "/old_location" to "/new_location"
    """
    if origin_path == "/":
        raise ValueError("Cannot move the root folder")

    user = User.query.filter_by(username=current_user.username).first()
    if user is None:
        raise ValueError(f"User '{current_user.username}' not found")

    posts: List[Post] = user.posts.all()
    filtered_posts = filter(
        lambda post: is_subpath(origin_path, post.folder_link), posts
    )

    for post in filtered_posts:
        if post.folder_link.startswith(origin_path):
            if dest_path != "/":
                post.folder_link = f"{dest_path}{post.folder_link[len(origin_path):]}"
            else:
                post.folder_link = "/"

    db.session.commit()


def get_webpage_title(url: str) -> Optional[str]:
    """
    Attempt to retrieve the title of a webpage from a given URL.

    This function tries various methods to extract the title:
    1. HTML <title> tag
    2. OpenGraph title meta tag
    3. Twitter card title meta tag
    4. First <h1> tag
    5. Using the newspaper library
    6. Falling back to the domain name

    Args:
        url (str): The URL of the webpage to fetch the title from.

    Returns:
        Optional[str]: The extracted title if successful, None if all methods fail.

    Raises:
        No exceptions are raised, but errors are logged.

    Example:
        >>> get_webpage_title("https://www.example.com")
        'Example Domain'
    """
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


def top_crop(img: Image.Image, target_size: Tuple[int, int]) -> Image.Image:
    """
    Crop and resize an image to fit the target size, focusing on the top part of the image.

    This function crops an image to maintain the aspect ratio specified by the target size,
    focusing on the top part of the image. It then resizes the cropped image to the target size.

    Args:
        img (Image.Image): The input image to be cropped and resized.
        target_size (Tuple[int, int]): The target size as a tuple (width, height).

    Returns:
        Image.Image: The cropped and resized image.

    Example:
        >>> from PIL import Image
        >>> img = Image.open("example.jpg")
        >>> resized_img = top_crop(img, (155, 155))
        >>> resized_img.show()
    """
    width, height = img.size
    target_ratio = target_size[0] / target_size[1]
    img_ratio = width / height

    if img_ratio > target_ratio:
        new_width = int(height * target_ratio)
        left = (width - new_width) // 2
        img = img.crop((left, 0, left + new_width, height))
    elif img_ratio < target_ratio:
        new_height = int(width / target_ratio)
        img = img.crop((0, 0, width, new_height))

    img = img.resize(target_size, Image.LANCZOS)

    return img


def image_preprocessing(image: Image.Image) -> Image.Image:
    """
    Preprocess an image by handling EXIF orientation and converting to RGB.

    This function opens an image, corrects its orientation based on EXIF data
    if available, and converts it to RGB color mode.

    Args:
        image (Union[str, BinaryIO]): The input image. Can be either a file path (str)
                                      or a file-like object (BinaryIO).

    Returns:
        Image.Image: The preprocessed image as a PIL Image object.

    Raises:
        FileNotFoundError: If the image file doesn't exist.
        PIL.UnidentifiedImageError: If the input is not a valid image file.
        OSError: For general OS-related errors, such as issues with reading the file.

    Example:
        >>> try:
        ...     preprocessed_img = image_preprocessing("path/to/image.jpg")
        ...     preprocessed_img.show()
        ... except Exception as e:
        ...     print(f"An error occurred: {e}")
    """
    try:
        img = Image.open(image)
        # Check for EXIF orientation and rotate if necessary
        if hasattr(img, "_getexif"):
            exif = img._getexif()
            if exif:
                orientation = exif.get(0x0112)
                if orientation:
                    if orientation == 3:
                        img = img.rotate(180, expand=True)
                    elif orientation == 6:
                        img = img.rotate(270, expand=True)
                    elif orientation == 8:
                        img = img.rotate(90, expand=True)

        # Ensure the image is in RGB mode
        if img.mode != "RGB":
            img = img.convert("RGB")

        return img
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Image file not found: {str(e)}") from e
    except Image.UnidentifiedImageError as e:
        raise Image.UnidentifiedImageError(f"Invalid image file: {str(e)}") from e
    except OSError as e:
        raise OSError(f"Error reading image file: {str(e)}") from e
    except Exception as e:
        raise ValueError(
            f"Unsupported input type or error processing image: {str(e)}"
        ) from e
