from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_redirect_to"
down_revision = "0010_equipment_and_paint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dove il sito ci ha mandati invece di rispondere. AutoScout non da' 404 per
    # ogni annuncio finito: a volte redirige alla pagina di lista del modello.
    #
    # Registrato ma NON ancora usato per classificare. Su 32 richieste di prova
    # il segnale sembrava fortissimo -- zero redirect fra le vendute e le
    # quarantene, otto su sedici fra quelle giudicate non-vendite -- ma quelle
    # etichette sono nostre inferenze, non verdetti del sito, e trentadue
    # richieste non fondano una regola che decide cos'e' una vendita.
    #
    # Si raccoglie per qualche settimana, poi si guarda se il redirect predice
    # davvero il ritiro. Se lo fa, diventa una prova diretta del sito al posto
    # di una nostra deduzione dalle impronte.
    op.add_column("listings", sa.Column("redirect_to", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("listings", "redirect_to")
