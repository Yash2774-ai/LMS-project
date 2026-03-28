from ..moodle_utils import moodle_api_call

def ensure_moodle_user(user):
    """
    Ensures that the Django user has a corresponding Moodle user.
    If moodle_user_id exists, returns it.
    If not, checks if user exists in Moodle by email, if yes, saves the ID.
    If not, creates a new Moodle user and saves the ID.
    """
    if user.moodle_user_id:
        return user.moodle_user_id

    # Check if user exists in Moodle by email
    user_res = moodle_api_call('core_user_get_users', {'criteria': [{'key': 'email', 'value': user.email}]})

    if user_res and isinstance(user_res, list) and len(user_res) > 0:
        moodle_user = user_res[0]
        user.profile.moodle_user_id = moodle_user['id']
        user.profile.save()
        return moodle_user['id']
    else:
        # Create new Moodle user
        firstname = user.first_name or "Student"
        lastname = user.last_name or "User"

        create_res = moodle_api_call('core_user_create_users', {'users': [{
            'username': user.email,
            'firstname': firstname,
            'lastname': lastname,
            'email': user.email,
            'auth': 'manual',
            'password': 'Temp@12345'
        }]})

        if create_res and isinstance(create_res, list) and len(create_res) > 0:
            moodle_user_id = create_res[0]['id']
            user.profile.moodle_user_id = moodle_user_id
            user.profile.save()
            return moodle_user_id
        else:
            # Handle error
            print(f"Failed to create Moodle user for {user.email}: {create_res}")
            return None