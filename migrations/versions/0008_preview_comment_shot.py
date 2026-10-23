"""Kommentare aus Testdurchläufen: Bild der Seite und technische Angaben

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-05 18:00:00

preview_comment.shot (file name of the image in instance/comment-shots/<survey uid>/), .shot_w and .shot_h (its size
in pixels) and .env (device, browser, window size, language and colour scheme it was written in). Columns already
there (built by create_all from the models of this version) are left as they are.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

COLUMNS = (('shot', sa.String(length=40)), ('shot_w', sa.Integer()), ('shot_h', sa.Integer()), ('env', sa.JSON()))


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('preview_comment')}
    missing = [(name, kind) for name, kind in COLUMNS if name not in have]
    if not missing:
        return
    with op.batch_alter_table('preview_comment', schema=None) as batch_op:
        for name, kind in missing:
            batch_op.add_column(sa.Column(name, kind, nullable=True))


def downgrade():
    with op.batch_alter_table('preview_comment', schema=None) as batch_op:
        for name, _ in reversed(COLUMNS):
            batch_op.drop_column(name)
