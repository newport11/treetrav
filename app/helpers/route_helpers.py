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
        return redirect(url_for(f"main.{route_type}"))
    elif request.method == "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(form.errors), 400
        return render_template(
            "feed.html", title=_(route_type.capitalize()), form=form
        )

    return await handle_get_request(form, route_type, user=user, username=username)


async def handle_get_request(form, route_type, user=None, username=None):
    if route_type in ["feed", "discover"]:
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


def get_cache_key(route_type, page, search_query):
    if current_user.is_authenticated:
        return f"{route_type}:user:{current_user.id}:{page}:{search_query}"
    return f"{route_type}:anon:{page}:{search_query}"
