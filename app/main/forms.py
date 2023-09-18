from flask import request
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField, BooleanField
from wtforms.validators import ValidationError, DataRequired, Length
from flask_babel import _, lazy_gettext as _l
from app.models import User, Post
import validators
import re



class SettingsForm(FlaskForm):
    username = StringField(_l('Username'), validators=[DataRequired()])
    email = TextAreaField(_l('Email'),
                             validators=[Length(min=0, max=140)])
    about_me = TextAreaField(_l('About me'),
                             validators=[Length(min=0, max=140)])
    private_mode = BooleanField(_l('Private Mode'), default=False)
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
            if not re.match(r'^[a-zA-Z0-9]+$', username.data.strip()):
                raise ValidationError(_('must only contain letters and numbers'))
            if not re.match(r'^[a-zA-Z][a-zA-Z0-9]*$', username.data.strip()):
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
    post_body = TextAreaField(_l('Description'), validators=[validate_body])
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