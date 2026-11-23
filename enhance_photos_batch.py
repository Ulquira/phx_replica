"""CLI entrypoint for batch technician photo enhancement via Gemini."""

from phx_sync.photos.enhancer import (
    ENHANCE_PROMPT,
    enhance_single_image,
    fix_image_orientation,
    process_all_photos,
)

__all__ = [
    "ENHANCE_PROMPT",
    "enhance_single_image",
    "fix_image_orientation",
    "process_all_photos",
]

if __name__ == "__main__":
    process_all_photos()
