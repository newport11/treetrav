from datetime import datetime
from flask import render_template, flash, redirect, url_for, request, g, \
    jsonify, current_app
from flask_login import current_user, login_required
from flask_babel import _, get_locale
from app import db
from app.main.forms import EditProfileForm, EmptyForm, PostForm, SearchForm
from app.models import User, Post
from app.main import bp
from app.favicon import get_favicon
from app.openai import generate_link_summary


@bp.before_app_request
def before_request():
    if current_user.is_authenticated:
        current_user.last_seen = datetime.utcnow()
        db.session.commit()
        g.search_form = SearchForm()
    g.locale = str(get_locale())


@bp.route('/', methods=['GET', 'POST'])
@bp.route('/home', methods=['GET', 'POST'])
@login_required
def home():
    form = PostForm()
    if form.validate_on_submit():
        post = Post(link=form.post_link.data, body=form.post_body.data, folder_link=form.post_folder.data.strip().strip("/") if form.post_folder.data else "/",
                       author=current_user)
        OPENAI_API_KEY = current_app.config["OPENAI_API_KEY"]
        if not post.body and OPENAI_API_KEY:
            post.body= generate_link_summary(post.link, OPENAI_API_KEY).rstrip(".")
        favicon_file_name = get_favicon(post.link)
        if favicon_file_name:
            post.favicon_file_name = favicon_file_name
        db.session.add(post)
        db.session.commit()
        flash(_('Your link is now posted!'))
        return redirect(url_for('main.home'))
    ''' 
    if request.method == 'POST' and request.headers.get('HX-Request'):
        # This is an HTMX request to load more posts
        page=posts.next_num if posts.has_next else None
        page = request.json.get('page', page)
        posts = current_user.followed_posts().paginate(
            page=page, per_page=current_app.config['POSTS_PER_PAGE'],
            error_out=False)
        return render_template('home2.html', title=_('Home'), form=form,
                           posts=posts.items)
    '''
    page = request.args.get('page', 1, type=int)
    posts = current_user.followed_posts().paginate(
        page=page, per_page=current_app.config['POSTS_PER_PAGE'],
        error_out=False)
    next_url = url_for('main.home', page=posts.next_num) \
        if posts.has_next else None
    prev_url = url_for('main.home', page=posts.prev_num) \
        if posts.has_prev else None
    return render_template('home.html', title=_('Home'), form=form,
                           posts=posts.items, next_url=next_url,
                           prev_url=prev_url)


@bp.route('/post/delete/<int:post_id>', methods=['POST'])
@login_required
def delete_post(post_id):
    post = Post.query.filter_by(id=post_id).first_or_404()
    if current_user.id == post.user_id:
        db.session.delete(post)
        db.session.commit()
        flash('Link deleted.')
        return redirect(request.referrer)

@bp.route('/folder/delete/<path:folder_link>', methods=['POST'])
@login_required
def delete_folder(folder_link):
    posts = Post.query.filter_by(user_id=current_user.id).all()
    for post in posts:
        if  post.folder_link != None and post.folder_link.startswith(folder_link) and current_user.id == post.user_id:
            db.session.delete(post)
            db.session.commit()
    flash('Folder deleted.')
    previous_folder = folder_link.rstrip("/").rsplit("/", 1)[0]
    if len(folder_link.split("/")) <= 1:
        previous_folder = "/"
    return redirect(url_for('main.user', username=current_user.username)) if previous_folder == "/" else redirect(url_for('main.user_subfolder', 
                                                                                                                      username=current_user.username,
                                                                                                                      path=previous_folder))

@bp.route('/explore/')
@bp.route('/explore')
@login_required
def explore():
    page = request.args.get('page', 1, type=int)
    posts = Post.query.order_by(Post.timestamp.desc()).paginate(
        page=page, per_page=current_app.config['POSTS_PER_PAGE'],
        error_out=False)
    next_url = url_for('main.explore', page=posts.next_num) \
        if posts.has_next else None
    prev_url = url_for('main.explore', page=posts.prev_num) \
        if posts.has_prev else None
    return render_template('home.html', title=_('Explore'),
                           posts=posts.items, next_url=next_url,
                           prev_url=prev_url)


@bp.route('/user/<username>/', methods=['POST','GET'])
@bp.route('/user/<username>', methods=['POST','GET'] )
@login_required
def user(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get('page', 1, type=int)
    posts = user.posts.filter_by(folder_link="/").order_by(Post.timestamp.desc()).paginate(
        page=page, per_page=current_app.config['POSTS_PER_PAGE'],
        error_out=False)
    
    folders_tmp = user.posts.filter(Post.folder_link !="/").order_by(Post.timestamp.desc()).all()
    folders = []
    visited_folders = []
    for post in folders_tmp:
        post.folder_name = post.folder_link = post.folder_link.split("/")[0]
        if post.folder_name != "" and post.folder_name not in visited_folders:
            visited_folders.append(post.folder_name)
            folders.append(post)
    
    next_url = url_for('main.user', username=user.username,
                       page=posts.next_num) if posts.has_next else None
    prev_url = url_for('main.user', username=user.username,
                       page=posts.prev_num) if posts.has_prev else None
    form = EmptyForm()
    return render_template('user.html', user=user, posts=posts.items,
                           next_url=next_url, prev_url=prev_url, form=form, folders=folders)


@bp.route('/followers/<username>')
@login_required
def get_followers(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get('page', 1, type=int)
    followers = user.followers.order_by(User.id.desc()).paginate(
        page=page, per_page=current_app.config['POSTS_PER_PAGE'],
        error_out=False)
    
    next_url = url_for('main.user', username=user.username,
                       page=followers.next_num) if followers.has_next else None
    prev_url = url_for('main.user', username=user.username,
                       page=followers.prev_num) if followers.has_prev else None
    return render_template('followers.html', user=user, followers=followers.items,
                           next_url=next_url, prev_url=prev_url)

@bp.route('/following/<username>')
@login_required
def get_following(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get('page', 1, type=int)
    following = user.followed.order_by(User.id.desc()).paginate(
        page=page, per_page=current_app.config['POSTS_PER_PAGE'],
        error_out=False)
    
    next_url = url_for('main.user', username=user.username,
                       page=following.next_num) if following.has_next else None
    prev_url = url_for('main.user', username=user.username,
                       page=following.prev_num) if following.has_prev else None
    return render_template('following.html', user=user, following=following.items,
                           next_url=next_url, prev_url=prev_url)


@bp.route('/user/<username>/<path:path>', methods=['POST','GET'])
@login_required
def user_subfolder(username, path):
    user = User.query.filter_by(username=username).first_or_404()
    posts = user.posts.filter_by(folder_link=path).order_by(Post.timestamp.desc())
    folders_tmp = user.posts.filter(Post.folder_link !=path ).order_by(Post.timestamp.desc()).all()
    folders = []
    visited_folders = []
    for post in folders_tmp:
        if path not in post.folder_link:
            continue
        else:
            post.folder_name = post.folder_link.removeprefix(path).strip("/").split("/")[0]
            post.folder_link =  path + "/" + post.folder_name
            if post.folder_name != "" and post.folder_name not in visited_folders:
                visited_folders.append(post.folder_name)
                folders.append(post)
    splitPath = path.rstrip("/").rsplit("/", 1)
    prevPath = splitPath[0]
    current_folder = splitPath[-1]
    if len(path.split("/")) <= 1:
        user_home_page = True
    else:
        user_home_page = False

    form = EmptyForm()
    return render_template('user_subfolder.html', user=user, posts=posts,
                        form=form, folders=folders, prevPath=prevPath, user_home_page=user_home_page, current_folder=current_folder)


@bp.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    form = EditProfileForm(current_user.username)
    if form.validate_on_submit():
        current_user.username = form.username.data
        current_user.email = form.email.data
        current_user.about_me = form.about_me.data
        db.session.commit()
        flash(_('Your changes have been saved.'))
        return redirect(url_for('main.edit_profile'))
    elif request.method == 'GET':
        form.username.data = current_user.username
        form.email.data = current_user.email
        form.about_me.data = current_user.about_me
    return render_template('edit_profile.html', title=_('Edit Profile'),
                           form=form)


@bp.route('/follow/<username>', methods=['POST'])
@login_required
def follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_('User %(username)s not found.', username=username))
            return redirect(url_for('main.home'))
        if user == current_user:
            flash(_('You cannot follow yourself!'))
            return redirect(url_for('main.user', username=username))
        current_user.follow(user)
        db.session.commit()
        flash(_('You are following %(username)s!', username=username))
        return redirect(url_for('main.user', username=username))
    else:
        return redirect(url_for('main.home'))


@bp.route('/unfollow/<username>', methods=['POST'])
@login_required
def unfollow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_('User %(username)s not found.', username=username))
            return redirect(url_for('main.home'))
        if user == current_user:
            flash(_('You cannot unfollow yourself!'))
            return redirect(url_for('main.user', username=username))
        current_user.unfollow(user)
        db.session.commit()
        flash(_('You are not following %(username)s.', username=username))
        return redirect(url_for('main.user', username=username))
    else:
        return redirect(url_for('main.home'))


@bp.route('/search')
@login_required
def search():
    if not g.search_form.validate():
        return redirect(url_for('main.explore'))
    page = request.args.get('page', 1, type=int)
    posts, total = Post.search(g.search_form.q.data, page,
                               current_app.config['POSTS_PER_PAGE'])
    next_url = url_for('main.search', q=g.search_form.q.data, page=page + 1) \
        if total > page * current_app.config['POSTS_PER_PAGE'] else None
    prev_url = url_for('main.search', q=g.search_form.q.data, page=page - 1) \
        if page > 1 else None
    return render_template('search.html', title=_('Search'), posts=posts,
                           next_url=next_url, prev_url=prev_url)