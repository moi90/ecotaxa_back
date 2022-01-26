"""empty message

Revision ID: 55f81c16f28
Revises: 4eec682f3a3
Create Date: 2015-06-17 08:43:59.895189

"""

# revision identifiers, used by Alembic.
revision = '55f81c16f28'
down_revision = '4eec682f3a3'

import sqlalchemy as sa
from alembic import op


def upgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.add_column('temp_tasks', sa.Column('creationdate', sa.DateTime(), nullable=True))
    op.add_column('temp_tasks', sa.Column('lastupdate', sa.DateTime(), nullable=True))
    ### end Alembic commands ###


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('temp_tasks', 'lastupdate')
    op.drop_column('temp_tasks', 'creationdate')
    ### end Alembic commands ###
