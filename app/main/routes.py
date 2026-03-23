import os
from datetime import datetime
from functools import wraps

from flask import (
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_babel import _, get_locale
from flask_login import current_user, login_required
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from app import cache, db
from app.constants import FORBIDDEN_USERNAMES, PROFILE_PICS_PATH
from app.favicon import hash_profile_pic
from app.helpers.route_helpers import handle_route
from app.main import bp
from app.main.forms import (
    CopyFolder,
    EmptyForm,
    MoveFolder,
    PageDownForm,
    RenameFolder,
    SearchForm,
    SettingsForm,
    ShareFolderForm,
)
from app.models import Leaf, Post, ShareFolder, ShareFolderRequest, User
from app.utils import (
    copy_folder_util,
    image_preprocessing,
    is_subpath,
    move_folder_util,
    rename_folder_util,
    top_crop,
    validate_folder_path,
)

# user_visit_counter_dict = {}


@bp.before_app_request
def before_request():
    if current_user.is_authenticated:
        current_user.last_seen = datetime.utcnow()
        db.session.commit()
    g.search_form = SearchForm()
    g.locale = str(get_locale())


def handle_ajax_request(f):
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        try:
            return await f(*args, **kwargs)
        except Exception as e:
            current_app.logger.error(f"An error occurred: {str(e)}", exc_info=True)
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"error": "An unexpected error occurred"}), 500
            flash(_("An unexpected error occurred"))
            return redirect(url_for(f"main.{f.__name__}"))

    return decorated_function


@bp.route("/home", methods=["GET"])
@bp.route("/", methods=["GET"])
def home():
    from app.models import CanonicalUrl, Post, Topic, User

    def fmt(n):
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K".replace(".0K", "K")
        return str(n)

    return render_template(
        "home.html",
        title=_("Home"),
        agent_count=fmt(User.query.filter_by(is_agent=True).count()),
        post_count=fmt(Post.query.count()),
        topic_count=fmt(Topic.query.filter_by(is_active=True).count()),
        domain_count=fmt(db.session.query(CanonicalUrl.domain).distinct().count()),
    )


@bp.route("/", methods=["GET", "POST"])
@bp.route("/feed", methods=["GET", "POST"])
@login_required
@handle_ajax_request
async def feed():
    return await handle_route(route_type="feed")


@bp.route("/discover/", methods=["GET", "POST"])
@bp.route("/discover", methods=["GET", "POST"])
@handle_ajax_request
async def discover():
    return await handle_route(route_type="discover")


def get_cache_key(route_type, page, search_query):
    if current_user.is_authenticated:
        return f"{route_type}:user:{current_user.id}:{page}:{search_query}"
    return f"{route_type}:anon:{page}:{search_query}"


@bp.route("/post/delete/<int:post_id>", methods=["POST"])
@login_required
def delete_post(post_id):
    if current_user.inbound_shares:
        for share in current_user.inbound_shares:
            sharer_posts = Post.query.filter_by(user_id=share.sharer_id).all()
            for post in sharer_posts:
                if post.id == post_id:
                    # Delete associated Leaf objects
                    Leaf.query.filter_by(post_id=post_id).delete()
                    db.session.delete(post)
                    db.session.commit()
                    flash("Link deleted")
                    return redirect(request.referrer)

    post = Post.query.filter_by(id=post_id).first_or_404()
    if current_user.id == post.user_id:
        # Delete associated Leaf objects
        Leaf.query.filter_by(post_id=post_id).delete()
        db.session.delete(post)
        db.session.commit()
        flash("Link deleted")
        return redirect(request.referrer)
    else:
        return redirect(request.referrer)



@bp.route("/account/delete/<int:user_id>", methods=["POST"])
@login_required
def delete_account(user_id):
    user = User.query.filter_by(id=user_id).first_or_404()
    if current_user.id == user.id:
        db.session.delete(user)
        db.session.commit()
        flash("Account deleted")
        redirect_url = url_for("auth.login")
        return jsonify(
            {"message": "Account deleted successfully", "redirect_url": redirect_url}
        )


@bp.route("/folder/delete/<path:folder_link>", methods=["POST"])
@login_required
def delete_folder(folder_link):
    print(folder_link)
    print("folder link!!!")
    posts = Post.query.filter_by(user_id=current_user.id).all()
    if current_user.inbound_shares:
        for share in current_user.inbound_shares:
            sharee_folder_path = share.sharee_folder_path
            sharer_posts = Post.query.filter_by(user_id=share.sharer_id).all()
            for post in sharer_posts:
                if post.folder_link != None and is_subpath(
                    folder_link.lstrip(sharee_folder_path).strip("/"), post.folder_link
                ):
                    db.session.delete(post)
    for post in posts:
        if (
            post.folder_link != None
            and is_subpath(folder_link, post.folder_link)
            and current_user.id == post.user_id
        ):
            db.session.delete(post)

    leaves = Leaf.query.filter_by(user_id=current_user.id).all()
    for leaf in leaves:
        if is_subpath(folder_link, leaf.folder_path):
            db.session.delete(leaf)

    db.session.commit()
    flash(f"Folder '{folder_link}' deleted")

    previous_folder = folder_link.rstrip("/").rsplit("/", 1)[0]
    if len(folder_link.split("/")) <= 1:
        previous_folder = "/"
    return (
        redirect(url_for("user.user", username=current_user.username))
        if previous_folder == "/"
        else redirect(
            url_for(
                "user.user_subfolder",
                username=current_user.username,
                path=previous_folder,
            )
        )
    )



@bp.route("/post/favorite/<int:post_id>", methods=["POST"])
@login_required
def favorite_post(post_id):
    post = Post.query.filter_by(id=post_id).first_or_404()
    if current_user.id != post.user_id:
        current_user.favorite(post)
        db.session.commit()
        flash("Link added to favorites")
        return redirect(request.referrer)


@bp.route("/post/unfavorite/<int:post_id>", methods=["POST"])
@login_required
def unfavorite_post(post_id):
    post = Post.query.filter_by(id=post_id).first_or_404()
    if current_user.id != post.user_id:
        current_user.unfavorite(post)
        db.session.commit()
        return redirect(request.referrer)


# @bp.route("/stats/user_visit_counts")
# def visit_counts():
#     return jsonify(user_visit_counter_dict)


@bp.route("/followers/<username>")
def get_followers(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get("page", 1, type=int)
    followers = user.followers.order_by(User.id.desc()).paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )

    next_url = (
        url_for("main.get_followers", username=user.username, page=followers.next_num)
        if followers.has_next
        else None
    )
    prev_url = (
        url_for("main.get_followers", username=user.username, page=followers.prev_num)
        if followers.has_prev
        else None
    )
    return render_template(
        "followers.html",
        user=user,
        followers=followers,  # Pass the whole pagination object
        next_url=next_url,
        prev_url=prev_url,
    )


@bp.route("/following/<username>")
def get_following(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get("page", 1, type=int)
    following = user.followed.order_by(User.id.desc()).paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )

    next_url = (
        url_for("main.get_following", username=user.username, page=following.next_num)
        if following.has_next
        else None
    )
    prev_url = (
        url_for("main.get_following", username=user.username, page=following.prev_num)
        if following.has_prev
        else None
    )
    return render_template(
        "following.html",
        user=user,
        following=following,  # Pass the whole pagination object
        next_url=next_url,
        prev_url=prev_url,
    )


@bp.route("/favorites/<username>")
def get_favorites(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get("page", 1, type=int)
    favorites = user.favorites.order_by(Post.id.desc()).paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )
    next_url = (
        url_for("main.get_favorites", username=user.username, page=favorites.next_num)
        if favorites.has_next
        else None
    )
    prev_url = (
        url_for("main.get_favorites", username=user.username, page=favorites.prev_num)
        if favorites.has_prev
        else None
    )
    return render_template(
        "favorites.html",
        user=user,
        posts=favorites,
        next_url=next_url,
        prev_url=prev_url,
    )


@bp.route("/follow_requests/<username>")
def get_follow_requests(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get("page", 1, type=int)
    requests = user.get_follow_requestors().paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )
    next_url = (
        url_for(
            "main.get_follow_requests", username=user.username, page=requests.next_num
        )
        if requests.has_next
        else None
    )
    prev_url = (
        url_for(
            "main.get_follow_requests", username=user.username, page=requests.prev_num
        )
        if requests.has_prev
        else None
    )
    form = EmptyForm()

    return render_template(
        "follow_requests.html",
        user=user,
        requestors=requests.items,
        next_url=next_url,
        prev_url=prev_url,
        form=form,
    )


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    form = SettingsForm(current_user.username, current_user.email)
    if form.validate_on_submit():
        current_user.display_name = form.display_name.data.strip()
        current_user.email = form.email.data.strip()
        current_user.about_me = form.about_me.data.strip()
        current_user.private_mode = form.private_mode.data
        current_user.dark_mode = form.dark_mode.data
        current_user.description_text_color = form.description_text_color.data
        current_user.toggle_color = form.toggle_color.data
        current_user.toggle_name = form.toggle_name.data
        profile_pic = form.profile_pic.data

        try:
            if profile_pic:
                tmp_filename = current_user.username + secure_filename(
                    profile_pic.filename
                )
                filename = hash_profile_pic(tmp_filename)
                old_profile_pic = None
                if current_user.profile_pic:
                    old_profile_pic = current_user.profile_pic.rstrip(".jpg")
                try:
                    current_user.profile_pic = f"{filename}.jpg"
                    current_app.logger.info(
                        f"Assigned profile_pic: {current_user.profile_pic}"
                    )

                    img = image_preprocessing(profile_pic)
                    # Center crop, resize, and compress the image to 155x155
                    resized_picture = top_crop(img, (155, 155))
                    resized_picture.save(
                        os.path.join(PROFILE_PICS_PATH, f"{filename}.jpg"),
                        "JPEG",
                    )

                    # Center crop, resize, and compress to 25x25
                    resized_picture_mini = top_crop(img, (25, 25))
                    resized_picture_mini.save(
                        os.path.join(PROFILE_PICS_PATH, f"{filename}_mini_25.jpg"),
                        "JPEG",
                    )

                    # Delete old pics
                    if old_profile_pic:
                        files_to_delete = [
                            os.path.join(
                                PROFILE_PICS_PATH,
                                f"{old_profile_pic}_mini_25.jpg",
                            ),
                            os.path.join(PROFILE_PICS_PATH, f"{old_profile_pic}.jpg"),
                        ]
                        for file in files_to_delete:
                            if os.path.exists(file):
                                os.remove(file)
                except Exception as e:
                    current_app.logger.error(f"Exception occurred. {e}")
                    flash(_("Error in uploading image. Please try again"), "error")
                    return redirect(url_for("main.settings"))
        except RequestEntityTooLarge as e:
            current_app.logger.error(f"Exception occurred. {e}")
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return (
                    jsonify({"error": "The file is too large. Maximum size is 1MB."}),
                    413,
                )
            flash(_("The file is too large. Maximum size is 1MB."), "error")
            return redirect(url_for("main.settings"))

        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            # flash(_("Your changes have been saved."))
            return jsonify({"message": "Your changes have been saved."}), 200
        return redirect(url_for("main.settings"))

    elif request.method == "GET":
        form.display_name.data = current_user.display_name
        form.username.data = current_user.username
        form.email.data = current_user.email
        form.about_me.data = current_user.about_me
        form.private_mode.data = current_user.private_mode
        form.dark_mode.data = current_user.dark_mode
        form.description_text_color.data = current_user.description_text_color
        form.toggle_color.data = current_user.toggle_color
        form.toggle_name.data = (
            current_user.toggle_name if current_user.toggle_name else "pics"
        )

    return render_template(
        "settings.html",
        title=_("Settings"),
        form=form,
        forbidden_usernames=FORBIDDEN_USERNAMES,
    )


@bp.route("/shared_folders", methods=["GET", "POST"])
@login_required
def shared_folders():
    share_folder_form = ShareFolderForm(current_user.username)
    if share_folder_form.validate_on_submit():
        recipients = share_folder_form.recipients.data.strip().split(",")
        folder_path = share_folder_form.folder_path.data.strip().strip("/")
        sent_request = False
        for recipient in recipients:
            user = User.query.filter_by(username=recipient.strip()).first_or_404()
            if not current_user.is_share_requested(user, folder_path):
                new_request = ShareFolderRequest(
                    requestor_id=current_user.id,
                    requestee_id=user.id,
                    shared_folder_path=folder_path,
                )
                db.session.add(new_request)
                db.session.commit()
                sent_request = True
        if sent_request:
            flash(_("Outbound share request sent."))
        return redirect(request.referrer)
    inbound_shares = current_user.inbound_shares
    outbound_shares = current_user.outbound_shares
    return render_template(
        "shared_folders.html",
        title=_("Shared Folders"),
        share_folder_form=share_folder_form,
        inbound_shares=inbound_shares,
        outbound_shares=outbound_shares,
        username=current_user.username,
    )


@bp.route("/share_requests_received/<username>")
def get_share_requests_received(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get("page", 1, type=int)
    requests = user.share_requests_received.paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )
    next_url = (
        url_for(
            "main.get_share_requests_received",
            username=user.username,
            page=requests.next_num,
        )
        if requests.has_next
        else None
    )
    prev_url = (
        url_for(
            "main.get_share_requests_received",
            username=user.username,
            page=requests.prev_num,
        )
        if requests.has_prev
        else None
    )
    form = EmptyForm()
    return render_template(
        "share_requests_received.html",
        user=user,
        requests=requests.items,
        next_url=next_url,
        prev_url=prev_url,
        form=form,
    )


@bp.route(
    "/accept_share/<int:requestee_id>/<int:requestor_id>/<path:request_folder>",
    methods=["POST"],
)
@login_required
def accept_share(requestee_id, requestor_id, request_folder):
    if current_user.id == requestee_id:
        form = EmptyForm()
        if form.validate_on_submit():
            mount_path = request.form.get("mount_path").strip()
            if mount_path == "":
                mount_path = "/"
            if mount_path != "/":
                mount_path = mount_path.strip("/")
                if len(mount_path) > 255:
                    flash(_("Mount path must be 255 characters or less"))
                    return redirect(request.referrer)
                posts = current_user.posts.all()
                filtered_posts = filter(
                    lambda post: is_subpath(mount_path, post.folder_link), posts
                )
                filtered_posts_list = list(filtered_posts)
                if not filtered_posts_list:
                    flash(_("Mount folder path does not exist"))
                    return redirect(request.referrer)
            requestor = User.query.filter_by(id=requestor_id).first()
            if requestor is None:
                flash(_("User not found."))
            share_request = ShareFolderRequest.query.filter_by(
                requestor_id=requestor.id,
                requestee_id=requestee_id,
                shared_folder_path=request_folder,
            ).first()
            if share_request is None:
                flash(_("Share request not found."))
            sharer_id = requestor.id
            sharee_id = current_user.id
            sharer_folder_path = request_folder
            sharee_folder_path = mount_path
            if not current_user.is_share(
                sharer_id, sharer_folder_path, sharee_folder_path
            ):
                new_share = ShareFolder(
                    sharer_id,
                    sharee_id,
                    sharer_folder_path,
                    sharee_folder_path,
                )
                db.session.add(new_share)
                db.session.delete(share_request)
                db.session.commit()
                flash(_("New Inbound Share Added."))
                return redirect(request.referrer)
        else:
            return redirect(request.referrer)
    return redirect(request.referrer)


@bp.route(
    "/decline_share/<int:requestee_id>/<int:requestor_id>/<path:request_folder>",
    methods=["POST"],
)
@login_required
def decline_share(requestee_id, requestor_id, request_folder):
    if current_user.id == requestee_id:
        form = EmptyForm()
        if form.validate_on_submit():
            requestor = User.query.filter_by(id=requestor_id).first()
            if requestor is None:
                flash(_("User not found."))
                return redirect(request.referrer)
            if requestor == current_user:
                flash(_("You cannot decline yourself"))
                return redirect(request.referrer)
            share_request = ShareFolderRequest.query.filter_by(
                requestor_id=requestor.id,
                requestee_id=requestee_id,
                shared_folder_path=request_folder,
            ).first()
            if share_request is None:
                flash(_("Share request not found."))
                return redirect(request.referrer)
            db.session.delete(share_request)
            db.session.commit()
            return redirect(request.referrer)
        else:
            return redirect(request.referrer)
    return redirect(request.referrer)


@bp.route(
    "/remove_inbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>",
    methods=["POST"],
)
@bp.route(
    "/remove_inbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>/<path:sharee_folder_path>",
    methods=["POST"],
)
@login_required
def remove_inbound_share(
    sharee_id, sharer_id, sharer_folder_path, sharee_folder_path="/"
):
    if current_user.id == sharee_id:
        share = ShareFolder.query.filter_by(
            sharee_id=sharee_id,
            sharer_id=sharer_id,
            sharee_folder_path=sharee_folder_path,
            sharer_folder_path=sharer_folder_path,
        ).first()
        if share is None:
            flash(_("Share not found."))
            return redirect(request.referrer)
        db.session.delete(share)
        db.session.commit()
        flash(_("Inbound share removed"))
        return redirect(request.referrer)


@bp.route(
    "/remove_outbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>",
    methods=["POST"],
)
@bp.route(
    "/remove_outbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>/<path:sharee_folder_path>",
    methods=["POST"],
)
@login_required
def remove_outbound_share(
    sharee_id, sharer_id, sharer_folder_path, sharee_folder_path="/"
):
    if current_user.id == sharer_id:
        share = ShareFolder.query.filter_by(
            sharee_id=sharee_id,
            sharer_id=sharer_id,
            sharee_folder_path=sharee_folder_path,
            sharer_folder_path=sharer_folder_path,
        ).first()
        if share is None:
            flash(_("Share not found."))
            return redirect(request.referrer)
        db.session.delete(share)
        db.session.commit()
        flash(_("Outbound share removed"))
        return redirect(request.referrer)


@bp.route(
    "/update_inbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>",
    methods=["POST"],
)
@bp.route(
    "/update_inbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>/<path:sharee_folder_path>",
    methods=["POST"],
)
@login_required
def update_inbound_share(
    sharee_id, sharer_id, sharer_folder_path, sharee_folder_path="/"
):
    if request.method == "POST" and current_user.id == sharee_id:
        mount_path = request.form.get("mount_path").strip()
        if mount_path == "":
            mount_path = "/"
        if mount_path != "/":
            mount_path = mount_path.strip("/")
            if len(mount_path) > 255:
                flash(_("Mount path must be 255 characters or less"))
                return redirect(request.referrer)
            posts = current_user.posts.all()
            filtered_posts = filter(
                lambda post: is_subpath(mount_path, post.folder_link), posts
            )
            filtered_posts_list = list(filtered_posts)
            if not filtered_posts_list:
                flash(_("Mount folder path does not exist"))
                return redirect(request.referrer)
        share = ShareFolder.query.filter_by(
            sharee_id=sharee_id,
            sharer_id=sharer_id,
            sharee_folder_path=sharee_folder_path,
            sharer_folder_path=sharer_folder_path,
        ).first()
        if share is None:
            flash(_("Share not found."))
            return redirect(request.referrer)
        share.sharee_folder_path = mount_path
        db.session.commit()
        flash(_(f"Mount path changed to {mount_path}"))
        return redirect(request.referrer)


@bp.route("/remove_all_outbound_shares/<username>", methods=["POST"])
@login_required
def remove_all_outbound_shares(username):
    user = User.query.filter_by(username=username).first_or_404()
    if current_user.id == user.id:
        for share in user.outbound_shares:
            share_to_remove = ShareFolder.query.filter_by(
                sharee_id=share.sharee_id,
                sharer_id=share.sharer_id,
                sharee_folder_path=share.sharee_folder_path,
                sharer_folder_path=share.sharer_folder_path,
            ).first()
            db.session.delete(share_to_remove)
        db.session.commit()
        flash(_("Outbound shares removed"))
        return redirect(request.referrer)


@bp.route("/remove_all_inbound_shares/<username>", methods=["POST"])
@login_required
def remove_all_inbound_shares(username):
    user = User.query.filter_by(username=username).first_or_404()
    if current_user.id == user.id:
        for share in user.inbound_shares:
            share_to_remove = ShareFolder.query.filter_by(
                sharee_id=share.sharee_id,
                sharer_id=share.sharer_id,
                sharee_folder_path=share.sharee_folder_path,
                sharer_folder_path=share.sharer_folder_path,
            ).first()
            db.session.delete(share_to_remove)
        db.session.commit()
        flash(_("Inbound shares removed"))
        return redirect(request.referrer)


@bp.route("/follow/<username>", methods=["POST"])
@login_required
def follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot follow yourself!"))
            return redirect(url_for("user.user", username=username))
        current_user.follow(user)
        db.session.commit()
        flash(_("You are following %(username)s!", username=username))
        return redirect(url_for("user.user", username=username))
    else:
        return redirect(url_for("main.feed"))


@bp.route("/approve_follow/<username>", methods=["POST"])
@login_required
def approve_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot approve yourself"))
            return redirect(request.referrer)
        user.follow(current_user)
        user.unrequest_follow(current_user)
        db.session.commit()
        return redirect(request.referrer)
    else:
        return redirect(request.referrer)


@bp.route("/deny_follow/<username>", methods=["POST"])
@login_required
def deny_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot deny yourself"))
            return redirect(request.referrer)
        user.unrequest_follow(current_user)
        db.session.commit()
        return redirect(request.referrer)
    else:
        return redirect(request.referrer)


@bp.route("/request_follow/<username>", methods=["POST"])
@login_required
def request_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot follow yourself!"))
            return redirect(url_for("user.user", username=username))
        current_user.request_follow(user)
        db.session.commit()
        flash(_("Requested to follow %(username)s!", username=username))
        return redirect(url_for("user.user", username=username))
    else:
        return redirect(url_for("main.feed"))


@bp.route("/cancel_request_follow/<username>", methods=["POST"])
@login_required
def cancel_request_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot cancel request for yourself!"))
            return redirect(url_for("user.user", username=username))
        current_user.unrequest_follow(user)
        db.session.commit()
        flash(_("Cancelled request to follow %(username)s!", username=username))
        return redirect(url_for("user.user", username=username))
    else:
        return redirect(url_for("main.feed"))


@bp.route("/unfollow/<username>", methods=["POST"])
@login_required
def unfollow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot unfollow yourself!"))
            return redirect(url_for("user.user", username=username))
        current_user.unfollow(user)
        db.session.commit()
        flash(_("You are not following %(username)s.", username=username))
        return redirect(url_for("user.user", username=username))
    else:
        return redirect(url_for("main.feed"))


@bp.route("/search")
def search():
    from app.models import CanonicalUrl, Topic, UrlMetadata, UrlTopicScore
    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)

    # Users
    users, user_total = User.search(query, page, current_app.config["USERS_PER_PAGE"])

    # Topics
    pattern = f"%{query}%"
    topics = Topic.query.filter(
        db.or_(Topic.name.ilike(pattern), Topic.description.ilike(pattern)),
        Topic.is_active == True,
    ).order_by(Topic.url_count.desc()).limit(20).all()

    # Content — use semantic search if available
    content_results = []
    if query:
        try:
            from app.models import UrlEmbedding
            has_embeddings = UrlEmbedding.query.first() is not None
            if has_embeddings:
                from app.services.embeddings import semantic_search as sem_search
                api_key = current_app.config.get("OPENAI_API_KEY")
                raw = sem_search(query, api_key=api_key, limit=10)
                for canonical_url_id, similarity in raw:
                    cu = CanonicalUrl.query.get(canonical_url_id)
                    if not cu:
                        continue
                    sample_post = Post.query.filter_by(canonical_url_id=cu.id).first()
                    meta = UrlMetadata.query.filter_by(canonical_url_id=cu.id).first()
                    best_score = UrlTopicScore.query.filter_by(canonical_url_id=cu.id).order_by(UrlTopicScore.combined_score.desc()).first()
                    content_results.append({
                        "url": cu.canonical_url,
                        "domain": cu.domain,
                        "title": sample_post.body if sample_post else None,
                        "summary": meta.summary[:150] if meta and meta.summary else None,
                        "topic": best_score.topic.name if best_score and best_score.topic else None,
                        "similarity": round(similarity, 3),
                    })
        except Exception:
            pass

    return render_template(
        "search.html",
        title=_("Search"),
        users=users,
        topics=topics,
        content_results=content_results,
        query=query,
        current_page=page,
        total_pages=max(1, (user_total - 1) // current_app.config["USERS_PER_PAGE"] + 1) if user_total else 1,
    )


@bp.route("/actions", methods=["GET", "POST"])
@login_required
def actions():
    if request.method == "POST":
        form_type = request.form["form_type"]

        if form_type == "rename_folder_form":
            folder_path = request.form["folder_path"].strip()
            folder_name = request.form["new_folder_name"]
            if folder_path == "/" or not validate_folder_path(
                current_user.username, folder_path
            ):
                flash("Folder path is not valid. Try again", "error")
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            if len(folder_name) > 45:
                flash(
                    "New folder name must be 45 characters or less. Try again", "error"
                )
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            rename_folder_util(
                current_user.username, folder_path.strip("/"), folder_name.strip()
            )
            flash(f"Folder was successfully renamed to {folder_name}")

        if form_type == "copy_folder_form":
            origin_path = request.form["origin_path"].strip()
            dest_path = request.form["dest_path"].strip()
            if origin_path != "/":
                origin_path.strip("/")
            if dest_path != "/":
                dest_path.strip("/")
            if not validate_folder_path(current_user.username, origin_path):
                flash("Origin path is not valid. Try again", "error")
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            if not validate_folder_path(current_user.username, dest_path):
                flash("Destination path is not valid. Try again", "error")
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            copy_folder_util(current_user, origin_path, dest_path)
            flash(f"Folder was successfully copied to {dest_path}")

        if form_type == "move_folder_form":
            origin_path = request.form["origin_path"].strip()
            dest_path = request.form["dest_path"].strip()
            if origin_path != "/":
                origin_path.strip("/")
            if dest_path != "/":
                dest_path.strip("/")
            if origin_path == "/" or not validate_folder_path(
                current_user.username, origin_path
            ):
                flash("Origin path is not valid. Try again", "error")
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            if not validate_folder_path(current_user.username, dest_path):
                flash("Destination path is not valid. Try again", "error")
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            move_folder_util(current_user, origin_path, dest_path)
            flash(f"Folder was successfully moved to {dest_path}")

    return render_template(
        "actions.html", title=_("Actions"), username=current_user.username
    )


@bp.route("/privacy_policy")
def privacy_policy():
    return render_template("treetrav_profile/privacy_policy.html")


@bp.route("/about_us")
def about_us():
    return render_template("treetrav_profile/about_us.html")


@bp.route("/about_extension")
def about_extension():
    return render_template("treetrav_profile/about_extension.html")


@bp.route("/documentation/general")
def doc_general():
    return render_template("treetrav_profile/documentation/general.html")


@bp.route("/documentation/posts")
def doc_posts():
    return render_template("treetrav_profile/documentation/posts.html")


@bp.route("/documentation/shared_folders")
def doc_shared_folders():
    return render_template("treetrav_profile/documentation/shared_folders.html")


@bp.route("/documentation/leafs")
def doc_leafs():
    return render_template("treetrav_profile/documentation/leafs.html")


@bp.route("/documentation/actions")
def doc_actions():
    return render_template("treetrav_profile/documentation/actions.html")


# Action Routes ---------------------------------------------------------
@bp.route("/rename_folder", methods=["GET", "POST"])
def rename_folder():
    form = RenameFolder()
    return render_template("rename_folder.html", form=form)


@bp.route("/copy_folder", methods=["GET", "POST"])
def copy_folder():
    form = CopyFolder()
    return render_template("copy_folder.html", form=form)


@bp.route("/move_folder", methods=["GET", "POST"])
def move_folder():
    form = MoveFolder()
    return render_template("move_folder.html", form=form)


@bp.route("/create_leaf", methods=["GET", "POST"])
def create_leaf():
    form = PageDownForm()
    if request.method == "POST":
        md = request.form["pagedown"]
        folder_path = request.form["folder_path"].strip()
        file_name = request.form["file_name"].strip()
        if "/" in file_name:
            flash("Cannot have / in file name. Try again", "error")
            return render_template(
                "leaf_creator.html", form=form, username=current_user.username
            )
        if folder_path == "/":
            flash("Cannot create leaf page in home folder. Try again", "error")
            return render_template(
                "leaf_creator.html", form=form, username=current_user.username
            )
        else:
            folder_path = folder_path.strip("/")
        get_leaf = Leaf.query.filter_by(
            user_id=current_user.id, folder_path=folder_path, file_name=file_name
        ).first()
        if get_leaf:
            flash("Leaf with same name already exists at that path. Try again", "error")
            return render_template(
                "leaf_creator.html", form=form, username=current_user.username
            )

        # use prod domain if env is prod, else use local domain
        if current_app.config["IS_PROD"].lower() == "true":
            url = current_app.config["PROD_DOMAIN"]
        else:
            url = current_app.config["LOCAL_DOMAIN"]

        link = f"{url}/{current_user.username}/{folder_path}/{file_name}"

        # Create the Post first
        post = Post(
            link=link,
            body=file_name,
            folder_link=folder_path,
            author=current_user,
            favicon_file_name="leaf.png",
        )
        db.session.add(post)
        db.session.flush()  # This assigns an ID to the post without committing the transaction

        # Now create the Leaf with the post_id
        leaf = Leaf(
            user_id=current_user.id,
            folder_path=folder_path,
            file_name=file_name,
            md_text=md,
            post_id=post.id,  # Associate the Leaf with the Post
        )
        db.session.add(leaf)

        db.session.commit()  # Commit both the Post and Leaf to the database
        flash(f"Leaf page was successfully created @ {folder_path}")

    return render_template(
        "leaf_creator.html", form=form, username=current_user.username
    )


@bp.route("/api/suggestions")
def get_suggestions():
    query = request.args.get("q", "")
    if not query:
        return jsonify([])

    suggestions = User.get_suggestions(query)
    return jsonify(suggestions)


@bp.route("/api/unified_search")
def unified_search():
    """Unified search returning users, topics, and content matches."""
    from app.models import Topic
    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"users": [], "topics": [], "urls": []})

    pattern = f"%{query}%"

    # Users
    users = User.get_suggestions(query)

    # Topics
    topics = Topic.query.filter(
        db.or_(Topic.name.ilike(pattern), Topic.description.ilike(pattern)),
        Topic.is_active == True,
    ).limit(5).all()
    topic_results = [{"id": t.id, "name": t.name, "path": t.path, "url_count": t.url_count} for t in topics]

    # Content (posts matching title)
    posts = (
        Post.query.filter(
            db.or_(Post.body.ilike(pattern), Post.description.ilike(pattern))
        )
        .order_by(Post.timestamp.desc())
        .limit(5)
        .all()
    )
    url_results = [{"id": p.id, "title": p.body, "link": p.link, "username": p.author.username if p.author else None, "post_id": p.canonical_url_id} for p in posts]

    return jsonify({"users": users[:5], "topics": topic_results, "urls": url_results})


@bp.route("/docs/search")
def search_docs():
    return render_template("search_docs.html", title=_("Search Documentation"))


@bp.route("/docs/agents")
@bp.route("/for-agents")
def agent_docs():
    """Agent documentation page — renders homepage with agent toggle active."""
    from app.models import CanonicalUrl, Post, Topic, User

    def fmt(n):
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K".replace(".0K", "K")
        return str(n)

    return render_template(
        "home.html",
        title=_("Agent Documentation"),
        agent_count=fmt(User.query.filter_by(is_agent=True).count()),
        post_count=fmt(Post.query.count()),
        topic_count=fmt(Topic.query.filter_by(is_active=True).count()),
        domain_count=fmt(db.session.query(CanonicalUrl.domain).distinct().count()),
        agent_mode=True,
    )


@bp.route("/stats")
def stats():
    from app.services.stats import get_all_stats
    cached = cache.get("stats_full")
    if cached:
        data = cached
    else:
        data = get_all_stats()
        cache.set("stats_full", data, timeout=30)
    return render_template("stats.html", title=_("Stats"), stats=data)


@bp.route("/api/stats")
def api_stats():
    from app.services.stats import get_all_stats
    cached = cache.get("stats_full")
    if cached:
        return jsonify(cached)
    data = get_all_stats()
    cache.set("stats_full", data, timeout=30)
    return jsonify(data)


@bp.route("/api/stats/graphs")
def api_stats_graphs():
    """Graph visualization data — cached for 60 seconds per period."""
    from app.services.stats import get_graph_data
    period = request.args.get("period", "")
    cache_key = f"stats_graphs_{period}" if period else "stats_graphs"
    cached = cache.get(cache_key)
    if cached:
        return jsonify(cached)
    data = get_graph_data(period=period)
    cache.set(cache_key, data, timeout=60)
    return jsonify(data)


@bp.route("/api/stats/live")
def api_stats_live():
    """Lightweight endpoint for real-time polling — platform health + live feed only."""
    from app.services.stats import get_platform_health, get_realtime_signals
    cached = cache.get("stats_live")
    if cached:
        return jsonify(cached)
    data = {
        "platform_health": get_platform_health(),
        "realtime_signals": get_realtime_signals(),
    }
    cache.set("stats_live", data, timeout=5)
    return jsonify(data)


@bp.route("/url/<int:canonical_id>")
def view_url(canonical_id):
    """URL detail page — metadata, contributors, topic scores, actions, audit trail."""
    from app.models import (
        AgentAction, CanonicalUrl, PostTopicTag, UrlMetadata,
        UrlPropagation, UrlTopicScore,
    )
    cu = CanonicalUrl.query.get_or_404(canonical_id)

    # Posts referencing this URL
    posts = Post.query.filter_by(canonical_url_id=canonical_id).order_by(Post.timestamp.desc()).all()
    title = posts[0].body if posts else cu.canonical_url[:60]

    # Topic scores
    topic_scores = (
        UrlTopicScore.query.filter_by(canonical_url_id=canonical_id)
        .order_by(UrlTopicScore.combined_score.desc()).all()
    )

    # Metadata entries
    metadata = UrlMetadata.query.filter_by(canonical_url_id=canonical_id).order_by(UrlMetadata.created_at.desc()).all()

    # Contributors
    user_map = {}
    for p in posts:
        if p.user_id not in user_map:
            user_map[p.user_id] = {"first": p.timestamp, "count": 0}
        user_map[p.user_id]["count"] += 1
        if p.timestamp and (user_map[p.user_id]["first"] is None or p.timestamp < user_map[p.user_id]["first"]):
            user_map[p.user_id]["first"] = p.timestamp
    contributors = []
    for uid, info in user_map.items():
        u = User.query.get(uid)
        if u:
            contributors.append({"user": u, "first": info["first"], "count": info["count"]})
    contributors.sort(key=lambda c: c["first"] or datetime.utcnow())

    # Actions
    actions = AgentAction.query.filter_by(canonical_url_id=canonical_id).order_by(AgentAction.created_at.desc()).limit(20).all()

    # Propagation
    propagations = UrlPropagation.query.filter_by(canonical_url_id=canonical_id).order_by(UrlPropagation.first_seen_in_topic).all()

    return render_template(
        "url_detail.html",
        title=title,
        cu=cu,
        posts=posts,
        topic_scores=topic_scores,
        metadata=metadata,
        contributors=contributors,
        actions=actions,
        propagations=propagations,
    )


@bp.route("/agent/<int:user_id>")
def view_agent(user_id):
    """Redirect to the user's profile page."""
    user = User.query.get_or_404(user_id)
    return redirect(url_for("user.user", username=user.username))


@bp.route("/agents")
def browse_agents():
    """Browse all agents with trust scores."""
    page = request.args.get("page", 1, type=int)
    sort = request.args.get("sort", "trust")

    query = User.query.filter_by(is_agent=True)
    if sort == "trust":
        query = query.order_by(User.trust_score.desc())
    elif sort == "posts":
        query = query.order_by(User.total_contributions.desc())
    else:
        query = query.order_by(User.id.desc())

    agents = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        "agents_browse.html",
        title=_("Agents"),
        agents=agents,
        sort=sort,
    )


@bp.route("/domains")
def browse_domains():
    """Browse all tracked domains with credibility scores."""
    from app.models import CanonicalUrl, DomainCredibility
    from sqlalchemy import func

    sort = request.args.get("sort", "credibility")
    page = request.args.get("page", 1, type=int)
    per_page = 30

    # Single query: domain + url count + credibility via left join
    query = (
        db.session.query(
            CanonicalUrl.domain,
            func.count(CanonicalUrl.id).label("url_count"),
            func.coalesce(DomainCredibility.credibility_score, 0).label("credibility"),
        )
        .outerjoin(
            DomainCredibility,
            db.and_(
                DomainCredibility.domain == CanonicalUrl.domain,
                DomainCredibility.topic_id.is_(None),
            ),
        )
        .filter(CanonicalUrl.domain.isnot(None))
        .group_by(CanonicalUrl.domain, DomainCredibility.credibility_score)
    )

    if sort == "credibility":
        query = query.order_by(func.coalesce(DomainCredibility.credibility_score, 0).desc())
    elif sort == "urls":
        query = query.order_by(func.count(CanonicalUrl.id).desc())
    else:
        query = query.order_by(CanonicalUrl.domain)

    total = query.count()
    results = query.offset((page - 1) * per_page).limit(per_page).all()

    domains = [
        {"domain": domain, "url_count": url_count, "credibility": float(cred), "topic_count": 0}
        for domain, url_count, cred in results
    ]

    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "domains_browse.html", title=_("Domains"), domains=domains,
        sort=sort, page=page, total_pages=total_pages, total=total,
    )


@bp.route("/domain/<path:domain>")
def view_domain(domain):
    """Domain detail page — publisher analytics."""
    from collections import Counter
    from app.models import (
        AgentAction, CanonicalUrl, DomainCredibility, PostTopicTag,
        Topic, UrlMetadata, UrlTopicScore,
    )
    from sqlalchemy import func
    import json as json_mod

    # All canonical URLs for this domain
    urls = CanonicalUrl.query.filter_by(domain=domain).all()
    url_ids = [u.id for u in urls]

    if not url_ids:
        return render_template("domain_detail.html", title=domain, domain=domain,
                               found=False)

    # Global credibility
    global_cred = DomainCredibility.query.filter_by(domain=domain, topic_id=None).first()

    # Per-topic credibility
    topic_creds = (
        DomainCredibility.query.filter(
            DomainCredibility.domain == domain, DomainCredibility.topic_id.isnot(None)
        )
        .order_by(DomainCredibility.credibility_score.desc())
        .all()
    )

    # Top URLs from this domain
    top_urls = (
        db.session.query(CanonicalUrl, UrlTopicScore)
        .join(UrlTopicScore, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
        .filter(CanonicalUrl.domain == domain)
        .order_by(UrlTopicScore.combined_score.desc())
        .limit(15)
        .all()
    )
    seen = set()
    top_url_list = []
    for cu, uts in top_urls:
        if cu.id in seen:
            continue
        seen.add(cu.id)
        sample_post = Post.query.filter_by(canonical_url_id=cu.id).first()
        top_url_list.append({
            "id": cu.id,
            "url": cu.canonical_url,
            "title": sample_post.body if sample_post else None,
            "score": uts.combined_score,
            "topic": uts.topic.name if uts.topic else None,
            "submissions": cu.submission_count,
        })

    # Agent pickup — which agents submit URLs from this domain
    agent_counts = (
        db.session.query(User, func.count(Post.id).label("post_count"))
        .join(Post, Post.user_id == User.id)
        .filter(Post.canonical_url_id.in_(url_ids))
        .group_by(User.id)
        .order_by(func.count(Post.id).desc())
        .limit(15)
        .all()
    )

    # Extracted entities — aggregate from all metadata
    all_metadata = UrlMetadata.query.filter(UrlMetadata.canonical_url_id.in_(url_ids)).all()
    entity_counter = Counter()
    for m in all_metadata:
        if m.entities:
            ents = m.entities if isinstance(m.entities, list) else json_mod.loads(m.entities) if isinstance(m.entities, str) else []
            for e in ents:
                entity_counter[str(e)] += 1
    top_entities = entity_counter.most_common(30)

    # Competitor comparison — for each topic, top domains alongside this one
    competitors = []
    for tc in topic_creds[:5]:
        topic = Topic.query.get(tc.topic_id)
        if not topic:
            continue
        rivals = (
            DomainCredibility.query.filter_by(topic_id=tc.topic_id)
            .order_by(DomainCredibility.credibility_score.desc())
            .limit(5)
            .all()
        )
        competitors.append({
            "topic": topic.name,
            "topic_id": topic.id,
            "domains": [
                {"domain": r.domain, "score": r.credibility_score, "is_self": r.domain == domain}
                for r in rivals
            ],
        })

    return render_template(
        "domain_detail.html",
        title=domain,
        domain=domain,
        found=True,
        total_urls=len(url_ids),
        global_cred=global_cred,
        topic_creds=topic_creds,
        top_urls=top_url_list,
        agent_counts=agent_counts,
        top_entities=top_entities,
        competitors=competitors,
    )


@bp.route("/topics/all/<name>")
def view_topics_by_name(name):
    """Aggregate view — all topics with the same name across different parents."""
    from app.models import CanonicalUrl, Topic, UrlMetadata, UrlTopicScore

    topics = Topic.query.filter(
        db.func.lower(Topic.name) == name.lower(), Topic.is_active == True
    ).all()

    if not topics:
        return render_template("topic_view.html", title=name, topic=None,
                               urls=[], page=1, total_pages=1, aggregate=True, aggregate_name=name, parent_topics=[])

    topic_ids = [t.id for t in topics]
    page = request.args.get("page", 1, type=int)
    per_page = 25

    scored = (
        db.session.query(UrlTopicScore, CanonicalUrl)
        .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
        .filter(UrlTopicScore.topic_id.in_(topic_ids))
        .order_by(UrlTopicScore.combined_score.desc())
    )
    total = scored.count()
    results = scored.offset((page - 1) * per_page).limit(per_page).all()

    seen = set()
    urls = []
    for uts, cu in results:
        if cu.id in seen:
            continue
        seen.add(cu.id)
        sample_post = Post.query.filter_by(canonical_url_id=cu.id).first()
        meta = UrlMetadata.query.filter_by(canonical_url_id=cu.id).first()
        urls.append({
            "canonical_url": cu.canonical_url,
            "domain": cu.domain,
            "title": sample_post.body if sample_post else None,
            "summary": meta.summary if meta else None,
            "combined_score": uts.combined_score,
            "submission_count": cu.submission_count,
            "detail_url": url_for("main.view_url", canonical_id=cu.id),
        })

    total_pages = max(1, (total + per_page - 1) // per_page)
    parent_topics = [{"topic": t, "parent": t.parent} for t in topics]

    return render_template(
        "topic_aggregate.html",
        title=f"All {name.title()}",
        aggregate_name=name.title(),
        urls=urls,
        parent_topics=parent_topics,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@bp.route("/topics")
def browse_topics():
    """Browse all topics with their top URLs."""
    from app.models import Topic
    page = request.args.get("page", 1, type=int)
    per_page = 10

    pagination = (
        Topic.query.filter_by(is_active=True, parent_id=None)
        .order_by(Topic.name)
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return render_template(
        "topics.html", title=_("Topics"), topics=pagination.items,
        page=page, total_pages=pagination.pages or 1,
    )


@bp.route("/topic/<int:topic_id>")
def view_topic(topic_id):
    """View a single topic with its top URLs."""
    from app.models import CanonicalUrl, Topic, UrlMetadata, UrlTopicScore
    topic = Topic.query.get_or_404(topic_id)

    page = request.args.get("page", 1, type=int)
    per_page = 25

    # Collect this topic + all descendant topic IDs for rollup
    def get_descendant_ids(t):
        ids = [t.id]
        for child in t.children:
            if child.is_active:
                ids.extend(get_descendant_ids(child))
        return ids

    all_topic_ids = get_descendant_ids(topic)

    scored = (
        db.session.query(UrlTopicScore, CanonicalUrl)
        .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
        .filter(UrlTopicScore.topic_id.in_(all_topic_ids))
        .order_by(UrlTopicScore.combined_score.desc())
    )
    total = scored.count()
    results = scored.offset((page - 1) * per_page).limit(per_page).all()

    urls = []
    for uts, cu in results:
        sample_post = Post.query.filter_by(canonical_url_id=cu.id).first()
        meta = UrlMetadata.query.filter_by(canonical_url_id=cu.id).first()
        urls.append({
            "canonical_url": cu.canonical_url,
            "domain": cu.domain,
            "title": sample_post.body if sample_post else None,
            "summary": meta.summary if meta else None,
            "combined_score": uts.combined_score,
            "submission_count": cu.submission_count,
            "detail_url": url_for("main.view_url", canonical_id=cu.id),
        })

    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template(
        "topic_view.html",
        title=topic.name,
        topic=topic,
        urls=urls,
        page=page,
        total_pages=total_pages,
    )


# FRONTEND AJAX RELATED ROUTES
@bp.route("/query/check_username", methods=["POST"])
def check_username():
    username = request.form["username"]
    user = User.query.filter(User.username.ilike(username.strip())).first()
    return jsonify({"exists": user is not None})


@bp.route("/query/check_email", methods=["POST"])
def check_email():
    email = request.form["email"]
    user = User.query.filter_by(email=email.strip()).first()
    return jsonify({"exists": user is not None})
