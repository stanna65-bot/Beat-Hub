app.add_middleware(
    SessionMiddleware,
    secret_key=(
        os.getenv('SESSION_SECRET')
        or secrets.token_urlsafe(48)
    ),
    same_site='lax',
    https_only=(
        os.getenv(
            'SESSION_HTTPS_ONLY',
            'false'
        ).lower() == 'true'
    ),
    max_age=(
        int(
            os.getenv(
                'SESSION_MAX_AGE',
                str(60 * 60 * 24 * 30)
            )
        )
    )
)
