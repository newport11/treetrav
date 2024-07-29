import re

from flask_babel import _
from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    ValidationError,
    regexp,
)

from app.constants import FORBIDDEN_USERNAMES
from app.models import User


class LoginForm(FlaskForm):
    username = StringField(_l("Username or Email"), validators=[DataRequired()])
    password = PasswordField(_l("Password"), validators=[DataRequired()])
    remember_me = BooleanField(_l("Remember Me"))
    submit = SubmitField(_l("Sign In"))


class RegistrationForm(FlaskForm):
    username = StringField(_l("Username"), validators=[DataRequired(), Length(max=30)])
    email = StringField(_l("Email"), validators=[DataRequired(), Email()])
    password = PasswordField(
        _l("Password"),
        validators=[
            DataRequired(),
            Length(min=8, message="Password is too short"),
            Length(max=80),
        ],
    )
    password2 = PasswordField(
        _l("Repeat Password"),
        validators=[DataRequired(), EqualTo("password"), Length(max=80)],
    )
    submit = SubmitField(_l("Register"))

    def validate_username(self, username):
        user = User.query.filter(User.username.ilike(username.data.strip())).first()
        if user is not None:
            raise ValidationError(_("username already exists."))
        if not re.match(r"^[a-zA-Z0-9_-]+$", username.data.strip()):
            raise ValidationError(
                _("must only contain letters, numbers, underscores, and hyphens")
            )
        if not re.match(r"^[a-zA-Z].*$", username.data.strip()):
            raise ValidationError(_("must start with letter"))
        if username.data.strip() in FORBIDDEN_USERNAMES:
            raise ValidationError(_("This username is not allowed"))

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data.strip()).first()
        if user is not None:
            raise ValidationError(_("please use a different email address."))


class ResetPasswordRequestForm(FlaskForm):
    email = StringField(_l("Email"), validators=[DataRequired(), Email()])
    submit = SubmitField(_l("Request Password Reset"))

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user is None:
            raise ValidationError(_("There is no account associated with this email."))


class ResetPasswordForm(FlaskForm):
    password = PasswordField(_l("Password"), validators=[DataRequired()])
    password2 = PasswordField(
        _l("Repeat Password"), validators=[DataRequired(), EqualTo("password")]
    )
    submit = SubmitField(_l("Request Password Reset"))
