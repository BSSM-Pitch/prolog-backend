"""Decode and normalize uploads; never serve user-controlled HTML or SVG."""

import warnings
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core import errors
from app.core.config import settings

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 25_000_000


def normalize(data: bytes) -> tuple[bytes, int, int]:
    if not data or len(data) > MAX_BYTES:
        raise errors.InvalidInput("이미지는 5MB 이하로 선택해 주세요.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise errors.InvalidInput("JPG, PNG, WebP 이미지만 사용할 수 있어요.")
                if original.width * original.height > MAX_PIXELS:
                    raise errors.InvalidInput("이미지 크기는 2,500만 화소 이하로 선택해 주세요.")
                original.seek(0)
                picture = ImageOps.exif_transpose(original).convert("RGBA")
                picture.thumbnail((1600, 1600))
                out = BytesIO()
                picture.save(out, "WEBP", quality=85, method=4)
                return out.getvalue(), picture.width, picture.height
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise errors.InvalidInput("읽을 수 없는 이미지예요. 다른 파일을 선택해 주세요.") from exc


class ImageStorage:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=Config(s3={"addressing_style": "path"}) if settings.s3_endpoint_url else None,
        )

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType="image/webp",
            CacheControl="private, max-age=300",
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=settings.s3_bucket, Key=key)

    def url(self, key: str) -> str:
        return str(
            self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.s3_bucket, "Key": key},
                ExpiresIn=3600,
            )
        )


_image_storage: ImageStorage | None = None


def image_storage() -> ImageStorage:
    global _image_storage
    if _image_storage is None:
        _image_storage = ImageStorage()
    return _image_storage
