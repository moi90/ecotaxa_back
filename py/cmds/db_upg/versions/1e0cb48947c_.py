"""empty message

Revision ID: 1e0cb48947c
Revises: None
Create Date: 2015-06-15 19:38:16.596967

"""

# revision identifiers, used by Alembic.
revision = '1e0cb48947c'
down_revision = None

import sqlalchemy as sa
from alembic import op


def upgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.add_column('objects', sa.Column('object_link', sa.VARCHAR(length=255), nullable=True))
    ### end Alembic commands ###


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('objects', 'object_link')
    ### end Alembic commands ###
