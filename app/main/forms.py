from flask import request
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField 
from wtforms.validators import ValidationError, DataRequired, Length
from flask_babel import _, lazy_gettext as _l
from app.models import User, Post
import validators



class EditProfileForm(FlaskForm):
    username = StringField(_l('Username'), validators=[DataRequired()])
    email = TextAreaField(_l('Email'),
                             validators=[Length(min=0, max=140)])
    about_me = TextAreaField(_l('About me'),
                             validators=[Length(min=0, max=140)])
    submit = SubmitField(_l('Save'))

    def __init__(self, original_username, *args, **kwargs):
        super(EditProfileForm, self).__init__(*args, **kwargs)
        self.original_username = original_username

    def validate_username(self, username):
        if username.data != self.original_username:
            user = User.query.filter_by(username=self.username.data).first()
            if user is not None:
                raise ValidationError(_('Please use a different username.'))


class EmptyForm(FlaskForm):
    submit = SubmitField('Submit')


class PostForm(FlaskForm):
    def validate_post(self, form):
        if validators.url(self.data['post_link']) is not True:
            raise ValidationError(_('Must post valid link'))
        
    def validate_body(self, form):
        length = len(self.data['post_body'])
        if length > 75:
            raise ValidationError(_(f'Must be 75 characters or less, currently {length}'))
        
    def validate_folder(self, form):
        folders = self.data['post_folder'].strip().strip("/").split("/")
        for folder in folders:
            if len(folder) > 30:
                raise ValidationError(_(f'Individual folder length must be under 30 characters, currently {len(folder)}'))
        
    post_link = TextAreaField(_l('Post a link'), validators=[DataRequired(), validate_post])
    post_body = TextAreaField(_l('Description'), validators=[validate_body])
    post_folder= TextAreaField(_l('Folder'), validators=[validate_folder])
    submit = SubmitField(_l('Submit'))


class SearchForm(FlaskForm):
    q = StringField(_l('Search'), validators=[DataRequired()])

    def __init__(self, *args, **kwargs):
        if 'formdata' not in kwargs:
            kwargs['formdata'] = request.args
        if 'meta' not in kwargs:
            kwargs['meta'] = {'csrf': False}
        super(SearchForm, self).__init__(*args, **kwargs)