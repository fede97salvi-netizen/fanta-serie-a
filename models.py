"""
Modelli SQLAlchemy — specchio fedele dello schema del database.

Scopo V3:
  1. Consentire ad Alembic di autogenerare le migrazioni future.
  2. Base per future riscritture delle query verso l'ORM.

Le query di run-time nella V3 usano ancora SQLAlchemy text() per
compatibilità (vedi db_utils.py). I modelli qui definiti non vengono
interrogati direttamente ma devono restare sincronizzati con lo schema.
"""

from extensions import db


class Utente(db.Model):
    __tablename__ = 'utenti'

    id               = db.Column(db.Integer, primary_key=True)
    nome_utente      = db.Column(db.Text, nullable=False, unique=True)
    password         = db.Column(db.Text, nullable=False)
    is_temp_password = db.Column(db.Boolean, nullable=False, default=False)
    is_admin         = db.Column(db.Boolean, nullable=False, default=False)
    email            = db.Column(db.Text, nullable=True)

    punteggio        = db.relationship('Punteggio',
                                       back_populates='utente', uselist=False)
    pronostici_iniziali = db.relationship('PronosticoIniziale',
                                          back_populates='utente', uselist=False)

    def __repr__(self) -> str:
        return f'<Utente {self.nome_utente}>'


class Partita(db.Model):
    __tablename__ = 'partite'

    id                      = db.Column(db.Integer, primary_key=True)
    giornata                = db.Column(db.Integer, nullable=False, index=True)
    squadra_casa            = db.Column(db.Text, nullable=False)
    squadra_ospite          = db.Column(db.Text, nullable=False)
    risultato_casa_reale    = db.Column(db.Integer, nullable=True)
    risultato_ospite_reale  = db.Column(db.Integer, nullable=True)
    marcatore_reale         = db.Column(db.Text, nullable=True)
    pronosticabile          = db.Column(db.Boolean, nullable=False, default=False)
    data_ora_partita        = db.Column(db.Text, nullable=True)

    pronostici = db.relationship('PronosticoGiornata', back_populates='partita')

    def __repr__(self) -> str:
        return f'<Partita G{self.giornata}: {self.squadra_casa} vs {self.squadra_ospite}>'


class PronosticoGiornata(db.Model):
    __tablename__ = 'pronostici_giornata'

    id                             = db.Column(db.Integer, primary_key=True)
    id_utente                      = db.Column(db.Integer,
                                                db.ForeignKey('utenti.id'),
                                                nullable=False, index=True)
    id_partita                     = db.Column(db.Integer,
                                                db.ForeignKey('partite.id'),
                                                nullable=False, index=True)
    esito_pronosticato             = db.Column(db.Text, nullable=True)
    risultato_casa_pronosticato    = db.Column(db.Integer, nullable=True)
    risultato_ospite_pronosticato  = db.Column(db.Integer, nullable=True)
    marcatore_pronosticato         = db.Column(db.Text, nullable=True)

    partita = db.relationship('Partita', back_populates='pronostici')

    def __repr__(self) -> str:
        return f'<Pronostico uid={self.id_utente} pid={self.id_partita}>'


class Punteggio(db.Model):
    __tablename__ = 'punteggi'

    id               = db.Column(db.Integer, primary_key=True)
    id_utente        = db.Column(db.Integer, db.ForeignKey('utenti.id'),
                                 nullable=False, unique=True)
    punteggio_totale = db.Column(db.Integer, nullable=False, default=0)

    utente = db.relationship('Utente', back_populates='punteggio')


class PunteggioGiornata(db.Model):
    __tablename__ = 'punteggi_giornata'

    id        = db.Column(db.Integer, primary_key=True)
    id_utente = db.Column(db.Integer, db.ForeignKey('utenti.id'),
                          nullable=False, index=True)
    giornata  = db.Column(db.Integer, nullable=False)
    punti     = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint('id_utente', 'giornata', name='uq_punteggio_giornata'),
    )


class StatoGiornata(db.Model):
    __tablename__ = 'stato_giornata'

    id            = db.Column(db.Integer, primary_key=True)
    giornata      = db.Column(db.Integer, nullable=False, unique=True)
    is_attiva     = db.Column(db.Boolean, nullable=False, default=False)
    is_in_archivio = db.Column(db.Boolean, nullable=False, default=False)


class StatoPronosticiIniziali(db.Model):
    __tablename__ = 'stato_pronostici_iniziali'

    id        = db.Column(db.Integer, primary_key=True)
    is_locked = db.Column(db.Boolean, nullable=False, default=False)


class PronosticoIniziale(db.Model):
    __tablename__ = 'pronostici_iniziali'

    id            = db.Column(db.Integer, primary_key=True)
    id_utente     = db.Column(db.Integer, db.ForeignKey('utenti.id'),
                               nullable=False, index=True)
    squadra_1     = db.Column(db.Text, nullable=True)
    squadra_2     = db.Column(db.Text, nullable=True)
    squadra_3     = db.Column(db.Text, nullable=True)
    squadra_4     = db.Column(db.Text, nullable=True)
    capocannoniere = db.Column(db.Text, nullable=True)

    utente = db.relationship('Utente', back_populates='pronostici_iniziali')


class RisultatiFinali(db.Model):
    __tablename__ = 'risultati_finali'

    id             = db.Column(db.Integer, primary_key=True)
    squadra_1      = db.Column(db.Text, nullable=True)
    squadra_2      = db.Column(db.Text, nullable=True)
    squadra_3      = db.Column(db.Text, nullable=True)
    squadra_4      = db.Column(db.Text, nullable=True)
    capocannoniere = db.Column(db.Text, nullable=True)


class Giocatore(db.Model):
    __tablename__ = 'giocatori'

    id             = db.Column(db.Integer, primary_key=True)
    nome_giocatore = db.Column(db.Text, nullable=False)
    squadra        = db.Column(db.Text, nullable=False, index=True)
