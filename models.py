"""
Modelli SQLAlchemy per Fanta Mondiali 2026.
Schema esteso rispetto a FantaSerieA con:
  - fasi: gironi / r32 / r16 / qf / sf / finale
  - pronostici_eliminazione: chi vince ogni match knockout
  - pronostici_torneo: vincitore, finalista, semi, capocannoniere (pre-torneo)
  - punteggi_fase: punti per fase eliminazione
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


class Partita(db.Model):
    __tablename__ = 'partite'
    id                     = db.Column(db.Integer, primary_key=True)
    giornata               = db.Column(db.Integer, nullable=True)   # round 1/2/3 nei gironi
    fase                   = db.Column(db.Text, nullable=False, default='gironi')
    girone                 = db.Column(db.Text, nullable=True)      # 'A'..'L' per gironi
    squadra_casa           = db.Column(db.Text, nullable=False)
    squadra_ospite         = db.Column(db.Text, nullable=False)
    risultato_casa_reale   = db.Column(db.Integer, nullable=True)
    risultato_ospite_reale = db.Column(db.Integer, nullable=True)
    # Nei gironi: gol dopo 90'. Knockout: gol dopo 90' (non extra time)
    gol_casa_90            = db.Column(db.Integer, nullable=True)
    gol_ospite_90          = db.Column(db.Integer, nullable=True)
    vincitore              = db.Column(db.Text, nullable=True)   # solo knockout
    marcatore_reale        = db.Column(db.Text, nullable=True)
    pronosticabile         = db.Column(db.Boolean, nullable=False, default=False)
    data_ora_partita       = db.Column(db.Text, nullable=True)


class PronosticoGiornata(db.Model):
    __tablename__ = 'pronostici_giornata'
    id                            = db.Column(db.Integer, primary_key=True)
    id_utente                     = db.Column(db.Integer, db.ForeignKey('utenti.id'), nullable=False)
    id_partita                    = db.Column(db.Integer, db.ForeignKey('partite.id'), nullable=False)
    esito_pronosticato            = db.Column(db.Text, nullable=True)
    risultato_casa_pronosticato   = db.Column(db.Integer, nullable=True)
    risultato_ospite_pronosticato = db.Column(db.Integer, nullable=True)
    marcatore_pronosticato        = db.Column(db.Text, nullable=True)


class PronosticoEliminazione(db.Model):
    """Pronostico per una singola partita della fase a eliminazione diretta."""
    __tablename__ = 'pronostici_eliminazione'
    id            = db.Column(db.Integer, primary_key=True)
    id_utente     = db.Column(db.Integer, db.ForeignKey('utenti.id'), nullable=False)
    id_partita    = db.Column(db.Integer, db.ForeignKey('partite.id'), nullable=False)
    vincitore     = db.Column(db.Text, nullable=True)    # nome squadra
    gol_casa_90   = db.Column(db.Integer, nullable=True) # risultato nei 90'
    gol_ospite_90 = db.Column(db.Integer, nullable=True)
    __table_args__ = (db.UniqueConstraint('id_utente', 'id_partita',
                                          name='uq_pron_elim'),)


class PronosticoTorneo(db.Model):
    """Pronostico torneo inserito prima dell'inizio (locked dopo)."""
    __tablename__ = 'pronostici_torneo'
    id             = db.Column(db.Integer, primary_key=True)
    id_utente      = db.Column(db.Integer, db.ForeignKey('utenti.id'),
                               nullable=False, unique=True)
    vincitore      = db.Column(db.Text, nullable=True)
    finalista      = db.Column(db.Text, nullable=True)
    semifinalista_1 = db.Column(db.Text, nullable=True)
    semifinalista_2 = db.Column(db.Text, nullable=True)
    capocannoniere = db.Column(db.Text, nullable=True)


class Punteggio(db.Model):
    __tablename__ = 'punteggi'
    id               = db.Column(db.Integer, primary_key=True)
    id_utente        = db.Column(db.Integer, db.ForeignKey('utenti.id'),
                                 nullable=False, unique=True)
    punteggio_totale = db.Column(db.Integer, nullable=False, default=0)


class PunteggioGiornata(db.Model):
    """Punti per ogni giornata della fase a gironi."""
    __tablename__ = 'punteggi_giornata'
    id        = db.Column(db.Integer, primary_key=True)
    id_utente = db.Column(db.Integer, db.ForeignKey('utenti.id'), nullable=False)
    giornata  = db.Column(db.Integer, nullable=False)
    punti     = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.UniqueConstraint('id_utente', 'giornata',
                                          name='uq_punt_giornata'),)


class PunteggioFase(db.Model):
    """Punti per ogni fase dell'eliminazione diretta."""
    __tablename__ = 'punteggi_fase'
    id        = db.Column(db.Integer, primary_key=True)
    id_utente = db.Column(db.Integer, db.ForeignKey('utenti.id'), nullable=False)
    fase      = db.Column(db.Text, nullable=False)  # r32/r16/qf/sf/finale
    punti     = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.UniqueConstraint('id_utente', 'fase',
                                          name='uq_punt_fase'),)


class StatoGiornata(db.Model):
    __tablename__ = 'stato_giornata'
    id             = db.Column(db.Integer, primary_key=True)
    giornata       = db.Column(db.Integer, nullable=False, unique=True)
    is_attiva      = db.Column(db.Boolean, nullable=False, default=False)
    is_in_archivio = db.Column(db.Boolean, nullable=False, default=False)


class StatoFase(db.Model):
    """Stato di ogni fase del torneo."""
    __tablename__ = 'stato_fase'
    id             = db.Column(db.Integer, primary_key=True)
    fase           = db.Column(db.Text, nullable=False, unique=True)
    is_attiva      = db.Column(db.Boolean, nullable=False, default=False)
    is_in_archivio = db.Column(db.Boolean, nullable=False, default=False)
    pronostici_locked = db.Column(db.Boolean, nullable=False, default=False)


class StatoPronosticiTorneo(db.Model):
    __tablename__ = 'stato_pronostici_torneo'
    id        = db.Column(db.Integer, primary_key=True)
    is_locked = db.Column(db.Boolean, nullable=False, default=False)


class RisultatiTorneo(db.Model):
    """Risultati reali del torneo (inseriti dall'admin a fine torneo)."""
    __tablename__ = 'risultati_torneo'
    id              = db.Column(db.Integer, primary_key=True)
    vincitore       = db.Column(db.Text, nullable=True)
    finalista       = db.Column(db.Text, nullable=True)
    semifinalista_1  = db.Column(db.Text, nullable=True)
    semifinalista_2  = db.Column(db.Text, nullable=True)
    capocannoniere  = db.Column(db.Text, nullable=True)


class Giocatore(db.Model):
    __tablename__ = 'giocatori'
    id             = db.Column(db.Integer, primary_key=True)
    nome_giocatore = db.Column(db.Text, nullable=False)
    squadra        = db.Column(db.Text, nullable=False)
