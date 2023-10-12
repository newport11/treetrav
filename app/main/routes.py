from datetime import datetime
import os
from flask import render_template, flash, redirect, url_for, request, g, \
    jsonify, current_app
from flask_login import current_user, login_required
from flask_babel import _, get_locale
from sqlalchemy import and_, func, or_, select, union, union_all
from app import db
from app.main.forms import CopyFolder, MoveFolder, PageDownForm, RenameFolder, SettingsForm, EmptyForm, PostForm, SearchForm, ShareFolderForm
from app.models import Leaf, ShareFolder, ShareFolderRequest, User, Post
from app.main import bp
from app.favicon import get_favicon, hash_profile_pic
from app.openai import generate_link_summary
from app.utils import copy_folder_util, is_subpath, move_folder_util, rename_folder_util, validate_folder_path
import markdown
from werkzeug.utils import secure_filename
from PIL import Image
from io import BytesIO





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
        folder_path = form.post_folder.data.strip().strip("/") if form.post_folder.data else "/"
        post = Post(link=form.post_link.data, body=form.post_body.data, folder_link=folder_path,
                       author=current_user)
        OPENAI_API_KEY = current_app.config["OPENAI_API_KEY"]
        if not post.body and OPENAI_API_KEY:
            post.body= generate_link_summary(post.link, OPENAI_API_KEY).rstrip(".")
        favicon_file_name = get_favicon(post.link)
        if favicon_file_name:
            post.favicon_file_name = favicon_file_name
        
        
        if current_user.inbound_shares and folder_path != '/':
            for share in current_user.inbound_shares:
                sharee_folder_path = share.sharee_folder_path
                sharer_folder_path = share.sharer_folder_path
                sharer_id = share.sharer_id
                if sharee_folder_path == '/':
                    path_to_check = sharer_folder_path.rstrip("/").rsplit("/", 1)[-1]
                else:
                    path_to_check = sharee_folder_path + '/' + sharer_folder_path.rstrip("/").rsplit("/", 1)[-1]
                if is_subpath(path_to_check, folder_path):
                    sharer = User.query.filter_by(id=sharer_id).first()
                    if sharer is None:
                        continue
                    else:
                        post.author = sharer
                        post.folder_link  = sharer_folder_path + post.folder_link[len(path_to_check):]
                        db.session.add(post)
                        db.session.commit()
                        flash(_('Your link is now posted!'))
                        return redirect(url_for('main.home'))

                                                           
        db.session.add(post)
        db.session.commit()
        flash(_('Your link is now posted!'))
        return redirect(url_for('main.home'))
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
    if current_user.inbound_shares:
        for share in current_user.inbound_shares:
            sharer_posts = Post.query.filter_by(user_id=share.sharer_id).all()
            for post in sharer_posts:
                if post.id == post_id:
                    db.session.delete(post)
                    db.session.commit()
                    flash('Link deleted')
                    return redirect(request.referrer)
    post = Post.query.filter_by(id=post_id).first_or_404()
    if current_user.id == post.user_id:
        db.session.delete(post)
        db.session.commit()
        flash('Link deleted')
        return redirect(request.referrer)
    else:
        return redirect(request.referrer)


@bp.route('/account/delete/<int:user_id>', methods=['POST'])
@login_required
def delete_account(user_id):
    user = User.query.filter_by(id=user_id).first_or_404()
    if current_user.id == user.id:
        db.session.delete(user)
        db.session.commit()
        flash('Account deleted')
        redirect_url = url_for('auth.login')
        return jsonify({'message': 'Account deleted successfully', 'redirect_url': redirect_url})
    

@bp.route('/folder/delete/<path:folder_link>', methods=['POST'])
@login_required
def delete_folder(folder_link):
    posts = Post.query.filter_by(user_id=current_user.id).all()
    if current_user.inbound_shares:
        for share in current_user.inbound_shares:
            sharee_folder_path = share.sharee_folder_path
            sharer_posts = Post.query.filter_by(user_id=share.sharer_id).all()
            for post in sharer_posts:
                if post.folder_link != None and is_subpath( folder_link.lstrip(sharee_folder_path).strip('/'), 
                                                           post.folder_link):
                    db.session.delete(post)
    for post in posts:
        if  post.folder_link != None and is_subpath( folder_link, post.folder_link) and current_user.id == post.user_id:
            db.session.delete(post)
    
    db.session.commit()
    flash('Folder deleted')
    previous_folder = folder_link.rstrip("/").rsplit("/", 1)[0]
    if len(folder_link.split("/")) <= 1:
        previous_folder = "/"
    return redirect(url_for('main.user', username=current_user.username)) if previous_folder == "/" else redirect(url_for('main.user_subfolder', 
                                                                                                                      username=current_user.username,
                                                                                                                      path=previous_folder))


@bp.route('/post/favorite/<int:post_id>', methods=['POST'])
@login_required
def favorite_post(post_id):
    post = Post.query.filter_by(id=post_id).first_or_404()
    if current_user.id != post.user_id:
        current_user.favorite(post)
        db.session.commit()
        flash('Link added to favorites')
        return redirect(request.referrer)


@bp.route('/post/unfavorite/<int:post_id>', methods=['POST'])
@login_required
def unfavorite_post(post_id):
    post = Post.query.filter_by(id=post_id).first_or_404()
    if current_user.id != post.user_id:
        current_user.unfavorite(post)
        db.session.commit()
        return redirect(request.referrer)


@bp.route('/explore/')
@bp.route('/explore')
@login_required
def explore():
    page = request.args.get('page', 1, type=int)
    posts = db.session.query(Post).join(User).filter(User.private_mode == False).order_by(Post.timestamp.desc()).paginate(
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
def user(username):
    user = User.query.filter_by(username=username).first_or_404()
    followers = user.followers
    form = EmptyForm()
    if current_user.get_id():
        is_following = current_user in followers
    else:
        is_following = False
    if (user.private_mode == True and user != current_user and not is_following)  :
        return render_template('user_private.html', user=user, form=form)
    else:  
        shared_id_list = []
        posts = user.posts.filter_by(folder_link="/").order_by(Post.timestamp.desc())
        #BEGIN OUTBOUND SHARE CODE
        outbound_shares = current_user.outbound_shares            
        for share in outbound_shares:
            sharer_folder_path = share.sharer_folder_path
            sharer = User.query.filter_by(id=share.sharer_id).first_or_404()
            if sharer is None:
                continue
            sharer_posts = sharer.posts.filter(or_(
                Post.folder_link.like(sharer_folder_path + '/%'),
                Post.folder_link == sharer_folder_path
            ))
            for post in sharer_posts:
                shared_id_list.append(post.id)

        # END OUTBOUND SHARE CODE

        
        # CHECK FOR INBOUND SHARES
        if current_user == user:
            inbound_shares = current_user.inbound_shares
            shared_folders_list = []
            
            for share in inbound_shares:
                sharer_folder_path = share.sharer_folder_path
                sharee_folder_path = share.sharee_folder_path
                sharer = User.query.filter_by(id=share.sharer_id).first_or_404()
                if sharer is None:
                    continue
                sharer_posts = sharer.posts.filter(or_(
                    Post.folder_link.like(sharer_folder_path + '/%'),
                    Post.folder_link == sharer_folder_path
                ))

                for post in sharer_posts:
                    post.folder_link = post.folder_link
                    post.author = current_user
                    post.user_id = current_user.id
                    shared_id_list.append(post.id)
                
            if shared_folders_list:
                original_query = user.posts.filter_by(folder_link="/")
                shared_folders_list.append(original_query)

                combined_query = union_all(*shared_folders_list)
                post_list = db.session.execute(combined_query).all()
                id_list = [post[0] for post in post_list ]

                posts = Post.query.filter(Post.id.in_(id_list)).order_by(Post.timestamp.desc()).all()
    

        # END INBOUND SHARE CODE

        page = request.args.get('page', 1, type=int)
        posts = posts.paginate(
            page=page, per_page=current_app.config['POSTS_PER_PAGE'],
            error_out=False)
        
        next_url = url_for('main.user', username=user.username,
                        page=posts.next_num) if posts.has_next else None
        prev_url = url_for('main.user', username=user.username,
                        page=posts.prev_num) if posts.has_prev else None
        
        folders_tmp = user.posts.filter(Post.folder_link !="/").order_by(Post.timestamp.desc()).all()
        folders = []
        visited_folders = []

        for post in folders_tmp:
            post.folder_name = post.folder_link = post.folder_link.split("/")[0]
            if shared_id_list:
                        post.is_shared = True if post.id in shared_id_list else False
            if post.folder_name != "" and post.folder_name not in visited_folders:
                visited_folders.append(post.folder_name)
                folders.append(post)
        

        return render_template('user.html', user=user, posts=posts.items,
                            next_url=next_url, prev_url=prev_url, form=form, folders=folders)


@bp.route('/followers/<username>')
def get_followers(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get('page', 1, type=int)
    followers = user.followers.order_by(User.id.desc()).paginate(
        page=page, per_page=current_app.config['POSTS_PER_PAGE'],
        error_out=False)
    
    next_url = url_for('main.get_followers', username=user.username,
                       page=followers.next_num) if followers.has_next else None
    prev_url = url_for('main.get_followers', username=user.username,
                       page=followers.prev_num) if followers.has_prev else None
    return render_template('followers.html', user=user, followers=followers.items,
                           next_url=next_url, prev_url=prev_url)

@bp.route('/following/<username>')
def get_following(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get('page', 1, type=int)
    following = user.followed.order_by(User.id.desc()).paginate(
        page=page, per_page=current_app.config['POSTS_PER_PAGE'],
        error_out=False)
    
    next_url = url_for('main.get_following', username=user.username,
                       page=following.next_num) if following.has_next else None
    prev_url = url_for('main.get_following', username=user.username,
                       page=following.prev_num) if following.has_prev else None
    return render_template('following.html', user=user, following=following.items,
                           next_url=next_url, prev_url=prev_url)


@bp.route('/favorites/<username>')
def get_favorites(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get('page', 1, type=int)
    favorites = user.favorites.order_by(Post.id.desc()).paginate(
        page=page, per_page=current_app.config['POSTS_PER_PAGE'],
        error_out=False)
    next_url = url_for('main.get_favorites', username=user.username,
                       page=favorites.next_num) if favorites.has_next else None
    prev_url = url_for('main.get_favorites', username=user.username,
                       page=favorites.prev_num) if favorites.has_prev else None
    return render_template('favorites.html', user=user, posts=favorites.items,
                           next_url=next_url, prev_url=prev_url)


@bp.route('/follow_requests/<username>')
def get_follow_requests(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get('page', 1, type=int)
    requests = user.get_follow_requestors().paginate(
        page=page, per_page=current_app.config['POSTS_PER_PAGE'],
        error_out=False)
    next_url = url_for('main.get_follow_requests', username=user.username,
                       page=requests.next_num) if requests.has_next else None
    prev_url = url_for('main.get_follow_requests', username=user.username,
                       page=requests.prev_num) if requests.has_prev else None
    form = EmptyForm()

    return render_template('follow_requests.html', user=user, requestors=requests.items,
                           next_url=next_url, prev_url=prev_url, form=form)


@bp.route('/user/<username>/<path:path>', methods=['POST','GET'])
def user_subfolder(username, path):
    user = User.query.filter_by(username=username).first_or_404()
    followers = user.followers
    form = EmptyForm()
    if current_user.get_id():
        is_following = current_user in followers
    else:
        is_following = False
    if (user.private_mode == True and user != current_user and not is_following)  :
        return render_template('user_private.html', user=user, form=form)
    else:
        splitPath = path.rstrip("/").rsplit("/", 1)
        prevPath = splitPath[0]
        current_folder = splitPath[-1]
        if len(path.split("/")) <= 1:
            user_home_page = True
        else:
            user_home_page = False

        if user.leafs:
            if path != '/':
                for leaf in user.leafs:
                    file_name = leaf.file_name
                    if current_folder == file_name:
                        get_leaf = Leaf.query.filter_by(user_id=user.id, folder_path = prevPath, file_name=file_name).first()
                        if get_leaf is None:
                            continue
                        else:
                            temp_html = markdown.markdown(get_leaf.md_text)
                            return render_template('leaf_page.html', user=user, 
                                form=form, user_home_page=user_home_page, temp_html=temp_html,  prevPath=prevPath)
        
        shared_id_list = []

        #BEGIN OUTBOUND SHARE CODE
        outbound_shares = current_user.outbound_shares            
        for share in outbound_shares:
            sharer_folder_path = share.sharer_folder_path
            sharer = User.query.filter_by(id=share.sharer_id).first_or_404()
            if sharer is None:
                continue
            sharer_posts = sharer.posts.filter(or_(
                Post.folder_link.like(sharer_folder_path + '/%'),
                Post.folder_link == sharer_folder_path
            ))
            for post in sharer_posts:
                shared_id_list.append(post.id)

        # END OUTBOUND SHARE CODE

        # CHECK FOR INBOUND SHARES
        if current_user == user:
            inbound_shares = current_user.inbound_shares
            shared_folders_list = []
            
            for share in inbound_shares:
                sharer_folder_path = share.sharer_folder_path
                sharee_folder_path = share.sharee_folder_path
                sharer = User.query.filter_by(id=share.sharer_id).first_or_404()
                if sharer is None:
                    continue
                sharer_posts = sharer.posts.filter(or_(
                    Post.folder_link.like(sharer_folder_path + '/%'),
                    Post.folder_link == sharer_folder_path
                ))

                for post in sharer_posts:
                    if sharee_folder_path == '/':
                        post.folder_link = post.folder_link
                    else:
                        post.folder_link = sharee_folder_path + '/' + post.folder_link
                    post.author = current_user
                    post.user_id = current_user.id
                    shared_id_list.append(post.id)
                    
                filtered_sharer_posts = sharer_posts.filter_by(folder_link=path)

                shared_folders_list.append(filtered_sharer_posts)
                
            if shared_folders_list:
                original_query = user.posts.filter_by(folder_link=path)
                shared_folders_list.append(original_query)

                combined_query = union_all(*shared_folders_list)
                post_list = db.session.execute(combined_query).all()
                id_list = [post[0] for post in post_list ]

                posts = Post.query.filter(Post.id.in_(id_list)).order_by(Post.timestamp.desc()).all()
    
            else:
                posts = user.posts.filter_by(folder_link=path).order_by(Post.timestamp.desc())

            folders_tmp = user.posts.filter(Post.folder_link !=path ).order_by(Post.timestamp.desc()).all()
            folders = []
            visited_folders = []
            for post in folders_tmp:
                if not is_subpath(path, post.folder_link):
                    continue
                else:
                    post.folder_name = post.folder_link.removeprefix(path).strip("/").split("/")[0]
                    post.folder_link =  path + "/" + post.folder_name
                    if shared_id_list:
                        post.is_shared = True if post.id in shared_id_list else False
                    if post.folder_name != "" and post.folder_name not in visited_folders:
                        visited_folders.append(post.folder_name)
                        folders.append(post)

            return render_template('user_subfolder.html', user=user, posts=posts,
                                form=form, folders=folders, prevPath=prevPath, user_home_page=user_home_page, current_folder=current_folder)
        # END INBOUND SHARE CODE


        posts = user.posts.filter_by(folder_link=path).order_by(Post.timestamp.desc())
        folders_tmp = user.posts.filter(Post.folder_link !=path ).order_by(Post.timestamp.desc()).all()
        folders = []
        visited_folders = []
        for post in folders_tmp:
            if not is_subpath(path, post.folder_link):
                continue
            else:
                post.folder_name = post.folder_link.removeprefix(path).strip("/").split("/")[0]
                post.folder_link =  path + "/" + post.folder_name
                if shared_id_list:
                    post.is_shared = True if post.id in shared_id_list else False
                if post.folder_name != "" and post.folder_name not in visited_folders:
                    visited_folders.append(post.folder_name)
                    folders.append(post)
        return render_template('user_subfolder.html', user=user, posts=posts,
                            form=form, folders=folders, prevPath=prevPath, user_home_page=user_home_page, current_folder=current_folder)


@bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    form = SettingsForm(current_user.username, current_user.email)
    if form.validate_on_submit():
        current_user.username = form.username.data.strip()
        current_user.email = form.email.data.strip()
        current_user.about_me = form.about_me.data.strip()
        current_user.private_mode = form.private_mode.data
        current_user.dark_mode = form.dark_mode.data
        picture = form.picture.data
        if picture:
            
            tmp_filename = current_user.username + secure_filename(picture.filename)
            filename = hash_profile_pic(tmp_filename)
            old_profile_pic = None
            if current_user.profile_pic:
                old_profile_pic = current_user.profile_pic.rstrip('.png')
            try:
                current_user.profile_pic = f'{filename}.png'
                img = Image.open(picture)
                resized_picture = img.resize((155, 155), Image.LANCZOS)
                output_buffer = BytesIO()
                resized_picture.save(output_buffer, format='PNG')  # You can change the format as needed
                output_buffer.seek(0)
                resized_picture.save(os.path.join('app/static/profile_pics', f'{filename}.png'))
                
                #resize to 25 as well
                resized_picture_mini = img.resize((25, 25), Image.LANCZOS)
                output_buffer = BytesIO()
                resized_picture_mini.save(output_buffer, format='PNG')  # You can change the format as needed
                output_buffer.seek(0)
                resized_picture_mini.save(os.path.join('app/static/profile_pics', f'{filename}_mini_25.png'))

                #delete old pics
                if old_profile_pic:
                    files_to_delete = [os.path.join('app/static/profile_pics', f'{old_profile_pic}_mini_25.png'),
                        os.path.join('app/static/profile_pics', f'{old_profile_pic}.png')]
                    for file in files_to_delete:
                        if os.path.exists(file):
                            os.remove(file)
            except:
                flash(_('Error in uploading image. Please try again'))
        db.session.commit()
        flash(_('Your changes have been saved.'))
        return redirect(url_for('main.settings'))

    elif request.method == 'GET':
        form.username.data = current_user.username
        form.email.data = current_user.email
        form.about_me.data = current_user.about_me
        form.private_mode.data = current_user.private_mode
        form.dark_mode.data = current_user.dark_mode
        
    return render_template('settings.html', title=_('Settings'),
                           form=form)

@bp.route('/shared_folders', methods=['GET', 'POST'])
@login_required
def shared_folders():
    share_folder_form = ShareFolderForm(current_user.username)
    if share_folder_form.validate_on_submit():
        recipients = share_folder_form.recipients.data.strip().split(",")
        folder_path = share_folder_form.folder_path.data.strip().strip("/")
        sent_request = False
        for recipient in recipients:
            user = User.query.filter_by(username=recipient.strip()).first_or_404()
            if not current_user.is_share_requested(user, folder_path ):
                new_request = ShareFolderRequest(
                    requestor_id=current_user.id,
                    requestee_id=user.id,
                    shared_folder_path=folder_path
                )
                db.session.add(new_request)
                db.session.commit()
                sent_request=True
        if sent_request:
            flash(_('Outbound share request sent.'))
        return redirect(request.referrer)
    inbound_shares = current_user.inbound_shares
    outbound_shares = current_user.outbound_shares
    return render_template('shared_folders.html', title=_('Shared Folders'),
                           share_folder_form=share_folder_form, inbound_shares=inbound_shares, outbound_shares=outbound_shares,
                             username=current_user.username)


@bp.route('/share_requests_received/<username>')
def get_share_requests_received(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get('page', 1, type=int)
    requests = user.share_requests_received.paginate(
        page=page, per_page=current_app.config['POSTS_PER_PAGE'],
        error_out=False)
    next_url = url_for('main.get_share_requests_received', username=user.username,
                       page=requests.next_num) if requests.has_next else None
    prev_url = url_for('main.get_share_requests_received', username=user.username,
                       page=requests.prev_num) if requests.has_prev else None
    form = EmptyForm()
    return render_template('share_requests_received.html', user=user, requests=requests.items,
                           next_url=next_url, prev_url=prev_url, form=form)


@bp.route('/accept_share/<int:requestee_id>/<int:requestor_id>/<path:request_folder>', methods=['POST'])
@login_required
def accept_share(requestee_id, requestor_id, request_folder):
    if current_user.id == requestee_id:
        form = EmptyForm()
        if form.validate_on_submit():
            mount_path = request.form.get('mount_path').strip()
            if mount_path != "/":
                mount_path = mount_path.strip("/")
                posts = current_user.posts.all()
                filtered_posts = filter(lambda post: is_subpath(mount_path, post.folder_link), posts)
                filtered_posts_list = list(filtered_posts)
                if not filtered_posts_list:
                        flash(_('Mount folder path does not exist'))
                        return redirect(request.referrer)
            requestor = User.query.filter_by(id=requestor_id).first()
            if requestor is None:
                flash(_('User not found.'))
            share_request = ShareFolderRequest.query.filter_by(
                requestor_id=requestor.id, requestee_id=requestee_id, shared_folder_path=request_folder).first()
            if share_request is None:
                flash(_('Share request not found.'))
            sharer_id=requestor.id
            sharee_id=current_user.id
            sharer_folder_path=request_folder
            sharee_folder_path=mount_path
            if not current_user.is_share(sharer_id, sharer_folder_path, sharee_folder_path ):
                new_share= ShareFolder(
                        sharer_id,
                        sharee_id,
                        sharer_folder_path,
                        sharee_folder_path,
                    )
                db.session.add(new_share)
                db.session.delete(share_request)
                db.session.commit()
                flash(_('New Inbound Share Added.'))
                return redirect(request.referrer)
        else:
            return redirect(request.referrer)
    return redirect(request.referrer)

@bp.route('/decline_share/<int:requestee_id>/<int:requestor_id>/<path:request_folder>', methods=['POST'])
@login_required
def decline_share(requestee_id, requestor_id, request_folder):
    if current_user.id == requestee_id:
        form = EmptyForm()
        if form.validate_on_submit():
            requestor = User.query.filter_by(id=requestor_id).first()
            if requestor is None:
                flash(_('User not found.'))
                return redirect(request.referrer)
            if requestor == current_user:
                flash(_('You cannot decline yourself'))
                return redirect(request.referrer)
            share_request = ShareFolderRequest.query.filter_by(
                requestor_id=requestor.id, requestee_id=requestee_id, shared_folder_path=request_folder).first()
            if share_request is None:
                flash(_('Share request not found.'))
                return redirect(request.referrer)
            db.session.delete(share_request)
            db.session.commit()
            return redirect(request.referrer)
        else:
            return redirect(request.referrer) 
    return redirect(request.referrer)


@bp.route('/remove_inbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>', methods=['POST'])
@bp.route('/remove_inbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>/<path:sharee_folder_path>', methods=['POST'])
@login_required
def remove_inbound_share(sharee_id, sharer_id, sharer_folder_path, sharee_folder_path='/'):
    if current_user.id == sharee_id:
        share = ShareFolder.query.filter_by(
        sharee_id=sharee_id, sharer_id=sharer_id, sharee_folder_path=sharee_folder_path,
             sharer_folder_path=sharer_folder_path).first()
        if share is None:
                flash(_('Share not found.'))
                return redirect(request.referrer)
        db.session.delete(share)
        db.session.commit()
        flash(_('Inbound share removed'))
        return redirect(request.referrer)


@bp.route('/remove_outbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>', methods=['POST'])
@bp.route('/remove_outbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>/<path:sharee_folder_path>', methods=['POST'])
@login_required
def remove_outbound_share(sharee_id, sharer_id, sharer_folder_path, sharee_folder_path='/'):
    if current_user.id == sharer_id:
        share = ShareFolder.query.filter_by(
        sharee_id=sharee_id, sharer_id=sharer_id, sharee_folder_path=sharee_folder_path,
             sharer_folder_path=sharer_folder_path).first()
        if share is None:
                flash(_('Share not found.'))
                return redirect(request.referrer)
        db.session.delete(share)
        db.session.commit()
        flash(_('Outbound share removed'))
        return redirect(request.referrer)


@bp.route('/remove_all_outbound_shares/<username>', methods=['POST'])
@login_required
def remove_all_outbound_shares(username):
    user = User.query.filter_by(username=username).first_or_404()
    if current_user.id == user.id:
        for share in user.outbound_shares:
            share_to_remove = ShareFolder.query.filter_by(
            sharee_id=share.sharee_id, sharer_id=share.sharer_id, sharee_folder_path=share.sharee_folder_path,
                sharer_folder_path=share.sharer_folder_path).first()
            db.session.delete(share_to_remove)
        db.session.commit()
        flash(_('Outbound shares removed'))
        return redirect(request.referrer)
    

@bp.route('/remove_all_inbound_shares/<username>', methods=['POST'])
@login_required
def remove_all_inbound_shares(username):
    user = User.query.filter_by(username=username).first_or_404()
    if current_user.id == user.id:
        for share in user.inbound_shares:
            share_to_remove = ShareFolder.query.filter_by(
            sharee_id=share.sharee_id, sharer_id=share.sharer_id, sharee_folder_path=share.sharee_folder_path,
                sharer_folder_path=share.sharer_folder_path).first()
            db.session.delete(share_to_remove)
        db.session.commit()
        flash(_('Inbound shares removed'))
        return redirect(request.referrer)
    

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


@bp.route('/approve_follow/<username>', methods=['POST'])
@login_required
def approve_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_('User %(username)s not found.', username=username))
            return redirect(url_for('main.home'))
        if user == current_user:
            flash(_('You cannot approve yourself'))
            return redirect(request.referrer)
        user.follow(current_user)
        user.unrequest_follow(current_user)
        db.session.commit()
        return redirect(request.referrer)
    else:
        return redirect(request.referrer)
    

@bp.route('/deny_follow/<username>', methods=['POST'])
@login_required
def deny_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_('User %(username)s not found.', username=username))
            return redirect(url_for('main.home'))
        if user == current_user:
            flash(_('You cannot deny yourself'))
            return redirect(request.referrer)
        user.unrequest_follow(current_user)
        db.session.commit()
        return redirect(request.referrer)
    else:
        return redirect(request.referrer)
    

@bp.route('/request_follow/<username>', methods=['POST'])
@login_required
def request_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_('User %(username)s not found.', username=username))
            return redirect(url_for('main.home'))
        if user == current_user:
            flash(_('You cannot follow yourself!'))
            return redirect(url_for('main.user', username=username))
        current_user.request_follow(user)
        db.session.commit()
        flash(_('Requested to follow %(username)s!', username=username))
        return redirect(url_for('main.user', username=username))
    else:
        return redirect(url_for('main.home'))


@bp.route('/cancel_request_follow/<username>', methods=['POST'])
@login_required
def cancel_request_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_('User %(username)s not found.', username=username))
            return redirect(url_for('main.home'))
        if user == current_user:
            flash(_('You cannot cancel request for yourself!'))
            return redirect(url_for('main.user', username=username))
        current_user.unrequest_follow(user)
        db.session.commit()
        flash(_('Cancelled request to follow %(username)s!', username=username))
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
    users, total = User.search(g.search_form.q.data, page,
                               current_app.config['POSTS_PER_PAGE'])
    next_url = url_for('main.search', q=g.search_form.q.data, page=page + 1) \
        if total > page * current_app.config['POSTS_PER_PAGE'] else None
    prev_url = url_for('main.search', q=g.search_form.q.data, page=page - 1) \
        if page > 1 else None
    return render_template('search.html', title=_('Search'), users=users,
                           next_url=next_url, prev_url=prev_url)


@bp.route('/actions', methods=['GET', 'POST'])
@login_required
def actions():
    if request.method == "POST":
        form_type = request.form['form_type']

        if form_type == 'rename_folder_form':
            folder_path = request.form['folder_path'].strip()
            folder_name = request.form['new_folder_name']
            if  folder_path == '/' or not validate_folder_path(current_user.username, folder_path):
                flash('Folder path is not valid. Try again', 'error')
                return render_template('actions.html', title=_('Actions'),
                           username=current_user.username)
            if len(folder_name) > 30:
                flash('New folder name must be 30 characters or less. Try again', 'error')
                return render_template('actions.html', title=_('Actions'),
                           username=current_user.username)
            rename_folder_util(current_user.username, folder_path.strip('/'), folder_name.strip())
            flash(f'Folder was successfully renamed to {folder_name}')

        if form_type == 'copy_folder_form':
            origin_path = request.form['origin_path'].strip()
            dest_path = request.form['dest_path'].strip()
            if origin_path != '/':
                origin_path.strip('/')
            if dest_path != '/':
                dest_path.strip('/')
            if not validate_folder_path(current_user.username, origin_path):
                flash('Origin path is not valid. Try again', 'error')
                return render_template('actions.html', title=_('Actions'),
                           username=current_user.username)
            if not validate_folder_path(current_user.username, dest_path):
                flash('Destination path is not valid. Try again', 'error')
                return render_template('actions.html', title=_('Actions'),
                           username=current_user.username)
            copy_folder_util(current_user, origin_path, dest_path)
            flash(f'Folder was successfully copied to {dest_path}')

        if form_type == 'move_folder_form':
            origin_path = request.form['origin_path'].strip()
            dest_path = request.form['dest_path'].strip()
            if origin_path != '/':
                origin_path.strip('/')
            if dest_path != '/':
                dest_path.strip('/')
            if origin_path == '/' or not validate_folder_path(current_user.username, origin_path):
                flash('Origin path is not valid. Try again', 'error')
                return render_template('actions.html', title=_('Actions'),
                           username=current_user.username)
            if not validate_folder_path(current_user.username, dest_path):
                flash('Destination path is not valid. Try again', 'error')
                return render_template('actions.html', title=_('Actions'),
                           username=current_user.username)
            move_folder_util(current_user, origin_path, dest_path)
            flash(f'Folder was successfully moved to {dest_path}')
            

    return render_template('actions.html', title=_('Actions'),
                           username=current_user.username)

@bp.route('/privacy_policy')
def privacy_policy():
    return render_template('privacy_policy.html')


# Action Routes ---------------------------------------------------------
@bp.route('/rename_folder', methods=['GET','POST'])
def rename_folder():
    form = RenameFolder()
    return render_template('rename_folder.html', form=form)

@bp.route('/copy_folder', methods=['GET','POST'])
def copy_folder():
    form = CopyFolder()
    return render_template('copy_folder.html', form=form)

@bp.route('/move_folder', methods=['GET','POST'])
def move_folder():
    form = MoveFolder()
    return render_template('move_folder.html', form=form)

@bp.route('/create_leaf', methods=['GET','POST'])
def create_leaf():
    form = PageDownForm()
    if request.method == "POST":
        md = request.form['pagedown']
        folder_path = request.form['folder_path'].strip()
        file_name = request.form['file_name'].strip()
        if '/' in file_name:
            flash('Cannot have / in file name. Try again', 'error')
            return render_template('leaf_creator.html', form=form, username=current_user.username)
        if folder_path == '/':
            flash('Cannot create leaf page in home folder. Try again', 'error')
            return render_template('leaf_creator.html', form=form, username=current_user.username)
        else:
            folder_path = folder_path.strip('/')
        get_leaf = Leaf.query.filter_by(user_id=current_user.id, folder_path = folder_path, file_name=file_name).first()
        if get_leaf:
            flash('Leaf with same name already exists at that path. Try again', 'error')
            return render_template('leaf_creator.html', form=form, username=current_user.username)
        leaf = Leaf(user_id=current_user.id, folder_path=folder_path, file_name=file_name, md_text = md)
        db.session.add(leaf)
        url = 'https://treetrav.com'
        link = f'{url}/user/{current_user.username}/{folder_path}/{file_name}'
        post = Post(link=link, body=file_name, folder_link=folder_path ,author=current_user, favicon_file_name='leaf.png')
        db.session.add(post)
        db.session.commit()
        flash(f'Leaf page was successfully created @ {folder_path}')

    return render_template('leaf_creator.html', form=form, username=current_user.username)