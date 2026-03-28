from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User, Group

class SignupForm(UserCreationForm):
    full_name = forms.CharField(max_length=100, required=True, widget=forms.TextInput(attrs={'placeholder': 'Full Name'}))
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'placeholder': 'Email Address'}))
    
    ROLE_CHOICES = [
        ('student', 'Student'),
        ('teacher', 'Teacher'),
    ]
    role = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.RadioSelect, initial='student')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make username optional as we will use email
        self.fields['username'].required = False
        self.fields['username'].widget = forms.HiddenInput()

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(username=email).exists():
            raise forms.ValidationError("A user with this email (username) already exists.")
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data['full_name']
        user.email = self.cleaned_data['email']
        user.username = self.cleaned_data['email'] # Use email as username
        if commit:
            user.save()
            role = self.cleaned_data.get('role', 'student')
            group_name = 'Teacher' if role == 'teacher' else 'Student'
            group, _ = Group.objects.get_or_create(name=group_name)
            user.groups.add(group)
            
            # Update the profile
            if hasattr(user, 'profile'):
                user.profile.role = role
                if role == 'teacher':
                    user.is_active = False # Deactivate teacher until approved
                    user.profile.is_approved = False
                    user.save()
                else:
                    user.profile.is_approved = True # Students are approved by default
                user.profile.save()
        return user

class CourseCreateForm(forms.Form):
    fullname = forms.CharField(
        max_length=254, 
        label="Course Fullname",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Introduction to Computer Science'})
    )
    shortname = forms.CharField(
        max_length=100, 
        label="Shortname",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. CS101'})
    )
    category_id = forms.IntegerField(
        label="Category ID",
        initial=1,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 1'})
    )
    description = forms.CharField(
        label="Description",
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Enter course description...'})
    )

class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'w-full px-4 py-3 rounded-xl border border-gray-200 focus:ring-2 focus:ring-blue-500 focus:border-transparent transition outline-none', 'placeholder': 'Full Name'}),
        }

from .models import UserProfile

class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['bio', 'designation', 'profile_picture']
        widgets = {
            'bio': forms.Textarea(attrs={'class': 'w-full px-4 py-3 rounded-xl border border-gray-200 focus:ring-2 focus:ring-blue-500 focus:border-transparent transition outline-none resize-none', 'rows': 4, 'placeholder': 'Tell students about your expertise...'}),
            'designation': forms.TextInput(attrs={'class': 'w-full px-4 py-3 rounded-xl border border-gray-200 focus:ring-2 focus:ring-blue-500 focus:border-transparent transition outline-none', 'placeholder': 'e.g. Senior Instructor'}),
        }

class EmailUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['email']
        widgets = {
            'email': forms.EmailInput(attrs={'class': 'w-full px-4 py-3 rounded-xl border border-gray-200 focus:ring-2 focus:ring-blue-500 focus:border-transparent transition outline-none'}),
        }

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.exclude(pk=self.instance.pk).filter(email=email).exists():
            raise forms.ValidationError("This email is already in use.")
        if User.objects.exclude(pk=self.instance.pk).filter(username=email).exists():
            raise forms.ValidationError("This email is already in use as a username.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data['email'] # Keep username in sync if we use email as username
        if commit:
            user.save()
        return user
