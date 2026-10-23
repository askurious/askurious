"""An old address of a blog entry leads on only while the entry is to be seen (security and code audit 2026-10-13,
TN-37): a withdrawn and renamed entry gave its draft's new name away."""
from datetime import timedelta

from app.extensions import db
from app.models import BlogPost, BlogSlug, utcnow


def test_old_address_of_a_withdrawn_entry_is_not_found(app, client):
    with app.app_context():
        post = BlogPost(slug="geheimer-neuer-titel", status=BlogPost.DRAFT, title_de="Geheim", body_de="x")
        db.session.add(post)
        db.session.flush()
        db.session.add(BlogSlug(slug="alter-titel", post_id=post.id))
        db.session.commit()
        pid = post.id
    guest = app.test_client()
    r = guest.get("/blog/alter-titel")
    assert r.status_code == 404 and "geheimer-neuer-titel" not in r.headers.get("Location", "")
    with app.app_context():
        post = db.session.get(BlogPost, pid)
        post.status, post.published_at = BlogPost.PUBLISHED, utcnow() - timedelta(hours=1)
        db.session.commit()
    r = guest.get("/blog/alter-titel")
    assert r.status_code == 301 and r.headers["Location"].endswith("/blog/geheimer-neuer-titel")
