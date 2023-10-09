from flask import request
from flask_wtf import FlaskForm
from sqlalchemy import func, text
from wtforms import StringField, SubmitField, TextAreaField, BooleanField, HiddenField
from wtforms.validators import ValidationError, DataRequired, Length
from flask_babel import _, lazy_gettext as _l
from app.models import User, Post
import validators
import re
from flask_wtf import Form
from flask_pagedown.fields import PageDownField
from wtforms.fields import SubmitField
from app.utils import is_subpath



class SettingsForm(FlaskForm):
    username = StringField(_l('Username'), validators=[DataRequired(), Length(max=30)])
    email = TextAreaField(_l('Email'),
                             validators=[Length(min=0, max=140)])
    about_me = TextAreaField(_l('About me'),
                             validators=[Length(min=0, max=140)])
    private_mode = BooleanField(_l('Private Mode'), default=False)
    dark_mode = BooleanField(_l('Dark Mode'), default=False)
    form_type = HiddenField('Form Type', default='settings_form')  # Include the form_type field as HiddenField
    submit = SubmitField(_l('Save'))

    def __init__(self, original_username, original_email, *args, **kwargs):
        super(SettingsForm, self).__init__(*args, **kwargs)
        self.original_username = original_username
        self.original_email = original_email

    def validate_username(self, username):
        if username.data != self.original_username:
            user = User.query.filter_by(username=self.username.data.strip()).first()
            if user is not None:
                raise ValidationError(_('username already exists.'))
            if not re.match(r'^[a-zA-Z0-9_-]+$', username.data.strip()):
                raise ValidationError(_('must only contain letters, numbers, underscores, and hyphens'))
            if not re.match(r'^[a-zA-Z].*$', username.data.strip()):
                raise ValidationError(_('must start with letter'))

    def validate_email(self, email):
        if email.data != self.original_email:
            user = User.query.filter_by(email=self.email.data.strip()).first()
            if user is not None:
                raise ValidationError(_('please use a different email.'))


class EmptyForm(FlaskForm):
    submit = SubmitField('Submit')


class PostForm(FlaskForm):
    def validate_post(self, form):
        if validators.url(self.data['post_link']) is not True:
            raise ValidationError(_('must post valid link'))
        
    def validate_body(self, form):
        length = len(self.data['post_body'])
        if length > 75:
            raise ValidationError(_(f'must be 75 characters or less, currently {length}'))
        
    def validate_folder(self, form):
        folders = self.data['post_folder'].strip().strip("/").split("/")
        for folder in folders:
            if len(folder) > 30:
                raise ValidationError(_(f'individual folder length must be under 30 characters, currently {len(folder)}'))
        
    post_link = TextAreaField(_l('Link*'), validators=[DataRequired(), validate_post])
    post_body = TextAreaField(_l('Title'), validators=[validate_body])
    post_folder= TextAreaField(_l('Folder'), validators=[validate_folder])
    submit = SubmitField(_l('Post'))


class SearchForm(FlaskForm):
    q = StringField(_l('Search Users'), validators=[DataRequired()])

    def __init__(self, *args, **kwargs):
        if 'formdata' not in kwargs:
            kwargs['formdata'] = request.args
        if 'meta' not in kwargs:
            kwargs['meta'] = {'csrf': False}
        super(SearchForm, self).__init__(*args, **kwargs)


class ShareFolderForm(FlaskForm):
    folder_path = StringField(_l('Folder Path:'), validators=[DataRequired(), Length(max=1000)])
    recipients = TextAreaField(_l('Share With: (usernames seperated by commas)'), validators=[DataRequired()])
    form_type = HiddenField('Form Type', default='share_folder_form')  # Include the form_type field as HiddenField
    submit = SubmitField(_l('Share Folder'))

    def __init__(self,username, *args, **kwargs):
        super(ShareFolderForm, self).__init__(*args, **kwargs)
        self.username = username

    def validate_folder_path(self, folder_path):
        if folder_path.data.strip() != "/":
            user = User.query.filter_by(username=self.username).first()
            folder_path = folder_path.data.strip().strip("/")
            posts = user.posts.all()
            filtered_posts = filter(lambda post: is_subpath(folder_path, post.folder_link), posts)
            filtered_posts_list = list(filtered_posts)
            if not filtered_posts_list:
                raise ValidationError(_(f'folder path {folder_path} does not exist.'))

    def validate_recipients(self, recipients):
        recipients_str = recipients.data.strip()
        if not re.match(r'^[,a-zA-Z0-9_\s-]*$', recipients_str):
                raise ValidationError(_('seperate each username by a comma'))
        
        recipients = recipients_str.split(",")
        for recipient in recipients:
            user = User.query.filter_by(username=recipient.strip()).first()
            if user is None:
                raise ValidationError(_(f'username {recipient} does not exist.'))


class RenameFolder(FlaskForm):
    folder_path = StringField(_l('Folder Path'), validators=[DataRequired(), Length(max=1000)])
    new_folder_name = StringField(_l('New Folder Name'), validators=[DataRequired()])
    form_type = HiddenField('Form Type', default='rename_folder_form') 

    submit = SubmitField(_l('Rename'))

    
class CopyFolder(FlaskForm):
    origin_path = StringField(_l('Origin Folder Path'), validators=[DataRequired(), Length(max=1000)])
    dest_path = StringField(_l('Destination Folder Path'), validators=[DataRequired(), Length(max=1000)])
    form_type = HiddenField('Form Type', default='copy_folder_form') 

    submit = SubmitField(_l('Copy'))


class MoveFolder(FlaskForm):
    origin_path = StringField(_l('Origin Folder Path'), validators=[DataRequired(), Length(max=1000)])
    dest_path = StringField(_l('Destination Folder Path'), validators=[DataRequired(), Length(max=1000)])
    form_type = HiddenField('Form Type', default='move_folder_form') 

    submit = SubmitField(_l('Move'))



class PageDownForm(FlaskForm):
    folder_path = StringField(_l('Folder Path'), validators=[DataRequired(), Length(max=1000)])
    file_name = StringField(_l('File Name'), validators=[DataRequired(), Length(max=75)])
    pagedown = PageDownField('Enter your markdown',validators=[DataRequired()])
    submit = SubmitField('Create Leaf Page')