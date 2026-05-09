"""
Supabase Storage helpers for uploaded chat images.
"""
import uuid
import os
from auth import get_admin_client

BUCKET = "chat-images"


def upload_image(image_bytes: bytes, content_type: str, user_id: str) -> str:
    """
    Upload image bytes to Supabase Storage and return the public URL.

    Args:
        image_bytes: Raw image bytes (from request.files.read())
        content_type: MIME type like 'image/jpeg' or 'image/png'
        user_id: User's UUID, used as folder prefix for organization

    Returns:
        Public URL of the uploaded image, or empty string on failure
    """
    try:
        # Pick file extension from content type
        ext_map = {
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/gif": "gif",
        }
        ext = ext_map.get(content_type, "jpg")

        # Path: chat-images/{user_id}/{random}.jpg
        # Folder-per-user keeps things organized and makes future cleanup easier
        filename = f"{user_id}/{uuid.uuid4().hex}.{ext}"

        admin = get_admin_client()
        admin.storage.from_(BUCKET).upload(
            path=filename,
            file=image_bytes,
            file_options={
                "content-type": content_type,
                "cache-control": "public, max-age=31536000",  # cache for 1 year
            },
        )

        public_url = admin.storage.from_(BUCKET).get_public_url(filename)
        return public_url

    except Exception as e:
        print(f"[storage] Upload failed: {e}")
        return ""
