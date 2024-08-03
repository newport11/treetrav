from flask import (
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_babel import _
from flask_login import current_user

from app import cache, db
from app.main.forms import EmptyForm, PostForm
from app.main.services import create_post, get_posts_query
from app.models import PostPic


async def handle_route(route_type: str, user=None, username=None):
    form = PostForm()
    route_type = route_type.lower()

    if request.method == "POST" and form.validate_on_submit():
        post, commit_to_db = await create_post(form, current_user)
        if commit_to_db:
            db.session.add(post)
            db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"message": "Your link is now posted!"}), 200
        flash(_("Your link is now posted!"))
        if route_type == "user_pics":
            return redirect(url_for("user.user_pics", username=username))
        else:
            return redirect(url_for(f"main.{route_type}"))
    elif request.method == "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(form.errors), 400
        if route_type == "user_pics":
            return render_template("user.html", title=_(username), form=form)
        else:
            return render_template(
                "feed.html", title=_(route_type.capitalize()), form=form
            )

    return await handle_get_request(form, route_type, user=user, username=username)


async def handle_get_request(form, route_type, user=None, username=None):
    if route_type in ["feed", "discover"]:
        # Existing logic for feed and discover routes
        page = request.args.get("page", 1, type=int)
        search_query = request.args.get("post_q", "")

        cache_key = get_cache_key(route_type, page, search_query)
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return cached_result

        posts = await get_posts_query(route_type, current_user, search_query, page)

        result = render_template(
            "feed.html",
            title=_(route_type.capitalize()),
            form=form,
            posts=posts.items,
            next_url=url_for(f"main.{route_type}", page=posts.next_num, q=search_query)
            if posts.has_next
            else None,
            prev_url=url_for(f"main.{route_type}", page=posts.prev_num, q=search_query)
            if posts.has_prev
            else None,
            current_page=posts.page,
            total_pages=posts.pages or 1,
            post_search_query=search_query,
        )
        cache.set(cache_key, result)
        return result
    elif route_type == "user_pics":
        # Logic for user_pics route
        posts = user.pic_posts.filter_by(folder_link="/").order_by(
            PostPic.timestamp.desc()
        )
        page = request.args.get("page", 1, type=int)
        posts = posts.paginate(
            page=page,
            per_page=current_app.config["PIC_POSTS_PER_PAGE"],
            error_out=False,
        )

        next_url = (
            url_for("user.user_pics", username=username, page=posts.next_num)
            if posts.has_next
            else None
        )
        prev_url = (
            url_for("user.user_pics", username=username, page=posts.prev_num)
            if posts.has_prev
            else None
        )

        folders_tmp = (
            user.pic_posts.filter(PostPic.folder_link != "/")
            .order_by(PostPic.timestamp.desc())
            .all()
        )
        folders = []
        visited_folders = []

        for post in folders_tmp:
            post.folder_name = post.folder_link = post.folder_link.split("/")[0]
            if post.folder_name != "" and post.folder_name not in visited_folders:
                visited_folders.append(post.folder_name)
                folders.append(post)

        # user_visit_counter_dict[f"user_{user.id}"] = user_visit_counter_dict.get(f"user_{user.id}", 0) + 1

        empty_form = EmptyForm()

        return render_template(
            "user_pics.html",
            user=user,
            posts=posts.items,
            next_url=next_url,
            prev_url=prev_url,
            form=form,
            empty_form=empty_form,
            folders=folders,
            current_page=posts.page,
            total_pages=posts.pages or 1,
        )
    else:
        # Handle other route types if needed
        pass


def get_cache_key(route_type, page, search_query):
    if current_user.is_authenticated:
        return f"{route_type}:user:{current_user.id}:{page}:{search_query}"
    return f"{route_type}:anon:{page}:{search_query}"
