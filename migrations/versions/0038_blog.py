"""Blog „Neuigkeiten“: blog_post, blog_image, blog_slug, user.blog_seen_at

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-10 10:00:00

The public news page /blog (app/blog.py; blog-konzept.md, Ralf 2026-10-10): entries written by site admins, their
images and earlier addresses, and per account the newest entry seen (the hint on the dashboard). Three new tables and
a column that may stay empty: they only add, so the deploy rolls. No foreign keys (like data_token): the rows go in code
with their entry and the account (accounts.delete), and a process of the code before, still running during a rolling
deploy, deletes an account without a key it does not know standing in its way. A table that is there already (built by create_all
from the models of this version) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0038'
down_revision = '0037'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    inspect = sa.inspect(op.get_bind())
    tables = set(inspect.get_table_names())
    if 'blog_post' not in tables:
        op.create_table('blog_post',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('slug', sa.String(length=100), nullable=False),
            sa.Column('status', sa.String(length=10), nullable=False),
            sa.Column('published_at', sa.DateTime(), nullable=True),
            sa.Column('content_changed_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('created_by_id', sa.Integer(), nullable=True),
            sa.Column('updated_by_id', sa.Integer(), nullable=True),
            sa.Column('category', sa.String(length=20), nullable=False),
            sa.Column('brands', sa.JSON(), nullable=True),
            sa.Column('title_de', sa.String(length=200), nullable=False),
            sa.Column('title_en', sa.String(length=200), nullable=False),
            sa.Column('summary_de', sa.Text(), nullable=False),
            sa.Column('summary_en', sa.Text(), nullable=False),
            sa.Column('body_de', sa.Text(), nullable=False),
            sa.Column('body_en', sa.Text(), nullable=False),
            sa.Column('cover_image_id', sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint('id', name=op.f('blog_post_pkey')),
            sa.UniqueConstraint('slug', name=op.f('blog_post_slug_key')))
        with op.batch_alter_table('blog_post', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_blog_post_status'), ['status'], unique=False)
            batch_op.create_index(batch_op.f('ix_blog_post_published_at'), ['published_at'], unique=False)
    if 'blog_image' not in tables:
        op.create_table('blog_image',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('post_id', sa.Integer(), nullable=False),
            sa.Column('filename', sa.String(length=40), nullable=False),
            sa.Column('width', sa.Integer(), nullable=False),
            sa.Column('height', sa.Integer(), nullable=False),
            sa.Column('alt_de', sa.String(length=300), nullable=False),
            sa.Column('alt_en', sa.String(length=300), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('blog_image_pkey')))
        with op.batch_alter_table('blog_image', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_blog_image_post_id'), ['post_id'], unique=False)
    if 'blog_slug' not in tables:
        op.create_table('blog_slug',
            sa.Column('slug', sa.String(length=100), nullable=False),
            sa.Column('post_id', sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint('slug', name=op.f('blog_slug_pkey')))
        with op.batch_alter_table('blog_slug', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_blog_slug_post_id'), ['post_id'], unique=False)
    if 'blog_seen_at' not in {c['name'] for c in inspect.get_columns('user')}:
        with op.batch_alter_table('user', schema=None) as batch_op:
            batch_op.add_column(sa.Column('blog_seen_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('blog_seen_at')
    op.drop_table('blog_slug')
    op.drop_table('blog_image')
    op.drop_table('blog_post')
