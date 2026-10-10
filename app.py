import streamlit as st
from bs4 import BeautifulSoup
import pandas as pd
import re
import json
import os
import tomli
from pathlib import Path
from supabase import create_client

# ============================================================
# CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="Suivi Commandes Shein",
    page_icon="🛍",
    layout="wide"
)

@st.cache_resource
def obtenir_supabase():
    if "SUPABASE_URL" in st.secrets:
        url = st.secrets["SUPABASE_URL"]
        cle = st.secrets["SUPABASE_KEY"]
    else:
        with open(
            ".streamlit/secrets.toml",
            "rb"
        ) as f:
            secrets = tomli.load(f)

        url = secrets["SUPABASE_URL"]
        cle = secrets["SUPABASE_KEY"]

    return create_client(url, cle)


supabase = obtenir_supabase()

st.title("🛍 Mon Suivi de Commandes Shein (Multi-Comptes & CRM)")

FICHIER_SAUVEGARDE = "sauvegarde_shein.json"


# ============================================================
# SYSTÈME DE SAUVEGARDE JSON
# ============================================================

def charger_sauvegarde():
    def charger_table(nom_table):
        resultat = (
            supabase.table(nom_table)
            .select("id, donnees")
            .execute()
        )

        lignes = []

        for element in resultat.data:
            ligne = dict(element["donnees"])
            ligne["_id"] = element["id"]
            lignes.append(ligne)

        return pd.DataFrame(lignes)

    try:
        df_comptes = charger_table("comptes")
        df_commandes = charger_table("commandes")

        return df_comptes, df_commandes

    except Exception as e:
        st.error(
            "Impossible de charger les données Supabase. "
            "Aucune donnée locale ne sera initialisée "
            "automatiquement."
        )
        st.error(str(e))
        st.stop()

def recharger_donnees():
    df_comptes, df_commandes = charger_sauvegarde()

    st.session_state.comptes = df_comptes
    st.session_state.mes_commandes = df_commandes

    # Réinitialiser le tableau éditable pour afficher les données à jour
    if "tableau_commandes" in st.session_state:
        del st.session_state["tableau_commandes"]

    if "tableau_comptes" in st.session_state:
        del st.session_state["tableau_comptes"]

def sauvegarder_table_supabase(nom_table, df):
    """
    Insère les nouvelles lignes et met à jour les existantes.
    Ne supprime aucune ligne de Supabase.
    """
    if "_id" not in df.columns:
        raise ValueError(
            "Identifiants manquants : sauvegarde annulée."
        )

    # Vérifier les identifiants avant toute écriture
    ids = df["_id"].dropna().tolist()

    if len(ids) != len(set(ids)):
        raise ValueError(
            "Identifiants en double : sauvegarde annulée."
        )

    for idx, ligne in df.iterrows():
        donnees = ligne.to_dict()
        identifiant = donnees.pop("_id", None)

        # Convertir les valeurs Pandas en valeurs JSON
        donnees = json.loads(
            json.dumps(
                donnees,
                ensure_ascii=False,
                default=str,
                allow_nan=False
            )
        )

        if identifiant is not None and not pd.isna(identifiant):
            identifiant = int(identifiant)

            (
                supabase.table(nom_table)
                .update({"donnees": donnees})
                .eq("id", identifiant)
                .execute()
            )

        else:
            resultat = (
                supabase.table(nom_table)
                .insert({"donnees": donnees})
                .execute()
            )

            if not resultat.data:
                raise RuntimeError(
                    f"Échec de création dans {nom_table}."
                )

            df.at[idx, "_id"] = resultat.data[0]["id"]

def sauvegarder():
    try:
        sauvegarder_table_supabase(
            "comptes",
            st.session_state.comptes
        )

        sauvegarder_table_supabase(
            "commandes",
            st.session_state.mes_commandes
        )

        st.session_state.comptes = (
            st.session_state.comptes.copy()
        )

        st.session_state.mes_commandes = (
            st.session_state.mes_commandes.copy()
        )

        return True

    except Exception as e:
        st.error(
            "Échec de la sauvegarde Supabase. "
            "Vérifie ta connexion et les données."
        )
        st.error(str(e))
        return False
# ============================================================
# INITIALISATION DE LA MÉMOIRE
# ============================================================

if (
    "comptes" not in st.session_state
    or "mes_commandes" not in st.session_state
):

    df_comptes, df_commandes = charger_sauvegarde()

    # --------------------------------------------------------
    # COMPTES
    # --------------------------------------------------------

    if (
        df_comptes is not None
        and not df_comptes.empty
    ):

        st.session_state.comptes = df_comptes

    else:

        st.session_state.comptes = pd.DataFrame({
            "Nom du compte": ["Compte A"],
            "Montant initial (€)": [100.0],
            "Coût d'achat": [0.0]
        })

    # --------------------------------------------------------
    # COMMANDES
    # --------------------------------------------------------

    if (
        df_commandes is not None
        and not df_commandes.empty
    ):

        st.session_state.mes_commandes = df_commandes

    else:

        st.session_state.mes_commandes = pd.DataFrame(
            columns=[
                "_id",
                "🗑️ Suppr.",
                "Client",
                "Téléphone",
                "Pseudo",
                "Type de pseudo",
                "Article / Lien",
                "SKU",
                "Compte",
                "Vendeur",
                "Prix",
                "Prix de vente",
                "⚠️ Vérifier prix",
                "Statut"
            ]
        )


# ============================================================
# FONCTIONS D'EXTRACTION
# ============================================================

def extraire_infos_html(html_content):

    soup = BeautifulSoup(
        html_content,
        "html.parser"
    )

    # --------------------------------------------------------
    # PRIX
    # --------------------------------------------------------

    prix_trouve = "Prix introuvable"

    tag_prix = soup.find(
        id="productMainPriceId"
    )

    if not tag_prix:

        tag_prix = soup.find(
            class_=lambda c:
            c and "productPrice__main" in c
        )

    if tag_prix:

        if tag_prix.has_attr("aria-label"):

            prix_brut = tag_prix[
                "aria-label"
            ].strip()

        else:

            prix_brut = tag_prix.get_text(
                strip=True
            )

        match = re.search(
            r"\d+[.,]\d+\s*€?",
            prix_brut
        )

        if match:

            prix_trouve = match.group(
                0
            ).replace(" ", "")

            if not prix_trouve.endswith("€"):

                prix_trouve += "€"

    # --------------------------------------------------------
    # PRIX DANS LE JSON
    # --------------------------------------------------------

    if (
        prix_trouve == "Prix introuvable"
        or not prix_trouve
    ):

        match_json = re.search(
            r'"salePrice"\s*:\s*\{.*?'
            r'"amountWithSymbol"\s*:\s*"([^"]+)"',
            html_content
        )

        if match_json:

            prix = match_json.group(1)

            prix_trouve = (
                prix
                if "€" in prix
                else f"{prix}€"
            )

        else:

            match_montant = re.search(
                r'"salePrice"\s*:\s*\{.*?'
                r'"amount"\s*:\s*"([\d\.]+)"',
                html_content
            )

            if match_montant:

                prix_trouve = (
                    f"{match_montant.group(1).replace('.', ',')}€"
                )

    # --------------------------------------------------------
    # DERNIÈRE TENTATIVE AVEC LES TEXTES CONTENANT €
    # --------------------------------------------------------

    if (
        prix_trouve == "Prix introuvable"
        or not prix_trouve
    ):

        textes_prix = soup.find_all(
            string=re.compile("€")
        )

        for texte in textes_prix:

            texte_propre = (
                texte.strip().lower()
            )

            if (
                "dès" in texte_propre
                or "livraison" in texte_propre
                or "code" in texte_propre
            ):
                continue

            match = re.search(
                r"\d+[.,]\d+\s*€",
                texte_propre
            )

            if match:

                prix_trouve = (
                    match.group(0)
                    .replace(" ", "")
                )

                break

    # --------------------------------------------------------
    # CONVERSION DU PRIX
    # --------------------------------------------------------

    prix_float = 0.0

    if (
        prix_trouve
        and prix_trouve != "Prix introuvable"
    ):

        try:

            prix_nettoye = re.sub(
                r"[^\d,.]",
                "",
                prix_trouve
            )

            prix_nettoye = (
                prix_nettoye.replace(",", ".")
            )

            prix_float = float(
                prix_nettoye
            )

        except ValueError:

            prix_float = 0.0

    # --------------------------------------------------------
    # SKU
    # --------------------------------------------------------

    sku_trouve = "N/A"

    match_sku = re.search(
        r"SKU:\s*([a-zA-Z0-9]+)",
        html_content,
        re.IGNORECASE
    )

    if match_sku:

        sku_trouve = match_sku.group(1)

    else:

        match_json_sku = re.search(
            r'"goods_sn"\s*:\s*"([a-zA-Z0-9]+)"',
            html_content
        )

        if match_json_sku:

            sku_trouve = (
                match_json_sku.group(1)
            )

        else:

            match_json_sku2 = re.search(
                r'"sku"\s*:\s*"([a-zA-Z0-9]+)"',
                html_content,
                re.IGNORECASE
            )

            if match_json_sku2:

                sku_trouve = (
                    match_json_sku2.group(1)
                )

    # --------------------------------------------------------
    # VENDEUR
    # --------------------------------------------------------

    vendeur_trouve = "SHEIN"

    tag_marche = soup.find(
        class_=lambda c:
        c and "soldbybox-header__corner-text" in c
    )

    if (
        tag_marche
        and "marché"
        in tag_marche.get_text(
            strip=True
        ).lower()
    ):

        vendeur_trouve = "Marketplace"

    else:

        tag_vendeur = soup.find(
            class_=lambda c:
            c and "soldbybox-header__title-text" in c
        )

        if tag_vendeur:

            texte_vendeur = (
                tag_vendeur.get_text(
                    strip=True
                )
            )

            if ":" in texte_vendeur:

                nom_vendeur = (
                    texte_vendeur
                    .split(":", 1)[1]
                    .strip()
                )

                if (
                    nom_vendeur.upper()
                    != "SHEIN"
                ):

                    vendeur_trouve = "Marketplace"

    return (
        prix_float,
        sku_trouve,
        vendeur_trouve
    )


# ============================================================
# COULEUR DES LIGNES
# ============================================================

def coloriser_ligne(row):

    val = row["Statut"]

    couleur = ""

    if val in [
        "🛒 Commandé",
        "📦 En préparation"
    ]:

        couleur = (
            "background-color: #D3D3D3;"
        )

    elif val in [
        "✈️ Expédié",
        "🚚 En cours de livraison"
    ]:

        couleur = (
            "background-color: #FFD580;"
        )

    elif val == "✅ Livré":

        couleur = (
            "background-color: #90EE90;"
        )

    elif val == "🚫 Colis refusé":

        return [
            "background-color: #FF7F7F;"
        ] * len(row)

    return [
        couleur
    ] * len(row)


# ============================================================
# TRI DES COMMANDES PAR PRIORITÉ
# ============================================================

def trier_commandes(df):

    ordre_statut = {

        # Priorité maximale
        "🚚 En cours de livraison": 0,

        # Ensuite
        "✈️ Expédié": 1,

        # Ensuite
        "📦 En préparation": 2,

        # Ensuite
        "🛒 Commandé": 3,

        # Tout en bas
        "✅ Livré": 4,

        # Dernier
        "🚫 Colis refusé": 5
    }

    df = df.copy()

    df["_ordre_statut"] = (
        df["Statut"]
        .map(ordre_statut)
        .fillna(99)
    )

    df = df.sort_values(
        by="_ordre_statut",
        ascending=True,
        kind="stable"
    )

    df = df.drop(
        columns=["_ordre_statut"]
    )

    df = df.reset_index(
        drop=True
    )

    return df


# ============================================================
# CALCUL PRIX DE VENTE
# ============================================================

def calculer_prix_vente(prix_achat):

    try:

        p = float(prix_achat)

        if p < 4.0:

            return (p * 2) * 320

        else:

            return (p + 4.0) * 320

    except (
        ValueError,
        TypeError
    ):

        return 0.0

if st.button("🔄 Recharger les données depuis Supabase"):
    recharger_donnees()
    st.rerun()
# ============================================================
# GESTION DES COMPTES
# ============================================================

st.markdown(
    "### 💳 Gestion des Comptes"
)

st.caption(
    "Ajoutez vos différents comptes ici. "
    "Ces informations serviront à déduire "
    "les achats et calculer la marge."
)

edited_comptes = st.data_editor(

    st.session_state.comptes[
        [
            "_id",
            "Nom du compte",
            "Montant initial (€)",
            "Coût d'achat"
        ]
    ],

    column_config={
        "_id": None,

        "Nom du compte":
            st.column_config.TextColumn(
                "Nom du compte",
                required=True
            ),

        "Montant initial (€)":
            st.column_config.NumberColumn(
                "Montant initial (€)",
                format="%.2f €",
                min_value=0.0
            ),

        "Coût d'achat":
            st.column_config.NumberColumn(
                "Coût d'achat",
                help="Ce que vous a coûté ce compte",
                format="%.2f"
            )
    },

    num_rows="dynamic",
    hide_index=True,
    width="stretch"
)


# Sauvegarde des comptes uniquement
# lorsqu'ils sont réellement modifiés.

if not edited_comptes.equals(
    st.session_state.comptes
):

    st.session_state.comptes = (
        edited_comptes
    )

    sauvegarder()


# ============================================================
# COMPTES DISPONIBLES
# ============================================================

comptes_calculs = (
    edited_comptes.copy()
)

comptes_calculs[
    "Montant initial (€)"
] = (
    comptes_calculs[
        "Montant initial (€)"
    ]
    .fillna(0.0)
)

comptes_calculs[
    "Coût d'achat"
] = (
    comptes_calculs[
        "Coût d'achat"
    ]
    .fillna(0.0)
)

noms_comptes = (
    edited_comptes[
        "Nom du compte"
    ]
    .dropna()
    .astype(str)
    .tolist()
)

noms_comptes = [
    nom
    for nom in noms_comptes
    if nom.strip() != ""
]

if not noms_comptes:

    noms_comptes = [
        "Aucun compte"
    ]


st.divider()


# ============================================================
# AJOUT D'ARTICLES
# ============================================================

st.markdown(
    "### ➕ Nouvelle Commande Client"
)

with st.form(
    "formulaire_ajout",
    clear_on_submit=True
):

    st.markdown(
        "#### 👤 Informations du client"
    )

    (
        col_nom,
        col_prenom,
        col_tel,
        col_type_pseudo,
        col_pseudo
    ) = st.columns(5)

    with col_nom:

        client_nom = st.text_input(
            "Nom"
        )

    with col_prenom:

        client_prenom = st.text_input(
            "Prénom"
        )

    with col_tel:

        client_tel = st.text_input(
            "Téléphone",
            placeholder="0555..."
        )

    with col_type_pseudo:

        type_pseudo = st.selectbox(
            "Type de pseudo",
            [
                "Instagram",
                "Facebook",
                "WhatsApp"
            ],
            index=0
        )

    with col_pseudo:

        client_pseudo = st.text_input(
            "Pseudo (Optionnel)",
            placeholder="@..."
        )

    st.markdown(
        "<br>",
        unsafe_allow_html=True
    )

    st.markdown(
        "#### 🛍️ Articles du client"
    )

    col1, col2 = st.columns(
        [4, 1]
    )

    with col1:

        fichiers_html = st.file_uploader(
            "1. Glissez le(s) fichier(s) HTML ici",
            type=["html", "htm"],
            accept_multiple_files=True
        )

    with col2:

        url_input = st.text_input(
            "2. Lien (Optionnel) :",
            placeholder="https://..."
        )

        st.markdown(
            "<br>",
            unsafe_allow_html=True
        )

        bouton_ajouter = (
            st.form_submit_button(
                "Ajouter à la liste",
                width="stretch",
                type="primary"
            )
        )


# ============================================================
# TRAITEMENT AJOUT COMMANDE
# ============================================================

if bouton_ajouter:

    if fichiers_html:

        nouvelles_commandes = []

        compte_par_defaut = (
            noms_comptes[0]
        )

        nom_complet = (
            f"{client_nom} {client_prenom}"
            .strip()
        )

        if not nom_complet:

            nom_complet = (
                "Client Inconnu"
            )

        for fichier in fichiers_html:

            html_source = (
                fichier
                .getvalue()
                .decode(
                    "utf-8",
                    errors="ignore"
                )
            )

            prix, sku, vendeur = (
                extraire_infos_html(
                    html_source
                )
            )

            nom_article = (
                url_input
                if (
                    len(fichiers_html) == 1
                    and url_input
                )
                else fichier.name
            )

            nouvelles_commandes.append({
                "_id" : None,

                "🗑️ Suppr.": False,

                "Client": nom_complet,

                "Téléphone": client_tel,

                "Pseudo": client_pseudo,

                "Type de pseudo": type_pseudo,

                "Article / Lien": nom_article,

                "SKU": sku,

                "Compte": compte_par_defaut,

                "Vendeur": vendeur,

                "Prix": prix,

                "Prix de vente":
                    calculer_prix_vente(
                        prix
                    ),

                # Nouveau rappel
                "⚠️ Vérifier prix": False,

                "Statut":
                    "🛒 Commandé"
            })

        df_nouveaux = pd.DataFrame(
            nouvelles_commandes
        )

        st.session_state.mes_commandes = (
            pd.concat(
                [
                    st.session_state.mes_commandes,
                    df_nouveaux
                ],
                ignore_index=True
            )
        )

        sauvegarder()

        st.success(
            f"{len(fichiers_html)} article(s) "
            f"ajouté(s) pour {nom_complet} "
            f"avec succès !"
        )

    else:

        st.warning(
            "Veuillez d'abord glisser "
            "au moins un fichier HTML."
        )


st.divider()


# ============================================================
# LISTE DES COMMANDES
# ============================================================

st.markdown(
    "### 📦 Ma Liste de Commandes"
)

st.caption(
    "Vous pouvez modifier plusieurs cellules "
    "et plusieurs lignes. Cliquez ensuite sur "
    "💾 Enregistrer les modifications."
)


if not st.session_state.mes_commandes.empty:

    # --------------------------------------------------------
    # TRI PAR PRIORITÉ
    # --------------------------------------------------------

    df = trier_commandes(
        st.session_state.mes_commandes
    )

    # --------------------------------------------------------
    # GARANTIR L'EXISTENCE DE LA COLONNE
    # --------------------------------------------------------

    if "⚠️ Vérifier prix" not in df.columns:

        df["⚠️ Vérifier prix"] = False

    # --------------------------------------------------------
    # GARANTIR L'EXISTENCE DU PRIX DE VENTE
    # --------------------------------------------------------

    if "Prix de vente" not in df.columns:

        df["Prix de vente"] = (
            df["Prix"]
            .apply(calculer_prix_vente)
        )

    # --------------------------------------------------------
    # RAPPEL DES PRIX À VÉRIFIER
    # --------------------------------------------------------

    commandes_a_verifier = df[
        df["⚠️ Vérifier prix"] == True
    ]

    if not commandes_a_verifier.empty:

        nombre_a_verifier = len(
            commandes_a_verifier
        )

        st.warning(
            f"⚠️ **{nombre_a_verifier} "
            f"article(s) ont un prix de vente "
            f"à vérifier !** "
            f"Pensez à contrôler le prix "
            f"avant de valider la commande."
        )

    # --------------------------------------------------------
    # COLORATION
    # --------------------------------------------------------

    df_colorise = (
        df.style.apply(
            coloriser_ligne,
            axis=1
        )
    )

    # --------------------------------------------------------
    # TABLEAU
    # --------------------------------------------------------

    edited_df = st.data_editor(

        df_colorise,

        key="tableau_commandes",

        # ----------------------------------------------------
        # ORDRE VISUEL DES COLONNES
        # ----------------------------------------------------

        column_order=[

            "🗑️ Suppr.",

            "Client",

            "Téléphone",

            "Pseudo",

            "Type de pseudo",

            "SKU",

            "Compte",

            "Vendeur",

            "Prix",

            "Prix de vente",

            "⚠️ Vérifier prix",

            "Statut",

            "Article / Lien"
        ],

        # ----------------------------------------------------
        # CONFIGURATION DES COLONNES
        # ----------------------------------------------------

        column_config={
            "_id": None,

            "🗑️ Suppr.":

                st.column_config.CheckboxColumn(
                    "Suppr.",
                    width=70
                ),

            "Client":

                st.column_config.TextColumn(
                    "Client",
                    width="medium"
                ),

            "Téléphone":

                st.column_config.TextColumn(
                    "Téléphone",
                    width="medium"
                ),

            "Pseudo":

                st.column_config.TextColumn(
                    "Pseudo",
                    width="medium"
                ),

            "Type de pseudo":

                st.column_config.SelectboxColumn(
                    "Type de pseudo",
                    options=[
                        "Instagram",
                        "Facebook",
                        "WhatsApp",
                        "Inconnu"
                    ],
                    width="small"
                ),

            "Article / Lien":

                st.column_config.LinkColumn(
                    "Article / Lien",
                    width="large"
                ),

            "SKU":

                st.column_config.TextColumn(
                    "SKU",
                    width=80
                ),

            "Compte":

                st.column_config.SelectboxColumn(
                    "Payé par",
                    options=noms_comptes,
                    required=True,
                    width="small"
                ),

            "Vendeur":

                st.column_config.TextColumn(
                    "Vendeur",
                    width="small"
                ),

            "Prix":

                st.column_config.NumberColumn(
                    "Prix d'achat",
                    format="%.2f €",
                    width=90
                ),

            "Prix de vente":

                st.column_config.NumberColumn(
                    "Prix de vente",
                    format="%.0f DZ",
                    width=100
                ),

            "⚠️ Vérifier prix":

                st.column_config.CheckboxColumn(
                    "⚠️ Vérifier",
                    help=(
                        "Cochez cette case si "
                        "vous n'êtes pas sûr "
                        "du prix de vente."
                    ),
                    width=80
                ),

            "Statut":

                st.column_config.SelectboxColumn(

                    "Statut",

                    options=[
                        "🛒 Commandé",
                        "📦 En préparation",
                        "✈️ Expédié",
                        "🚚 En cours de livraison",
                        "✅ Livré",
                        "🚫 Colis refusé"
                    ],

                    required=True,

                    width="medium"
                )
        },

        hide_index=True,

        width="stretch",

        num_rows="fixed"
    )

    # ========================================================
    # BOUTONS
    # ========================================================

    col_save, col_delete, col_reset = (
        st.columns([2, 2, 2])
    )

    # --------------------------------------------------------
    # ENREGISTRER
    # --------------------------------------------------------

    with col_save:

        if st.button(
            "💾 Enregistrer les modifications",
            type="primary",
            width="stretch"
        ):

            # -----------------------------------------------
            # Recalculer seulement les prix de vente
            # vides ou égaux à zéro.
            #
            # Les prix modifiés manuellement sont conservés.
            # -----------------------------------------------

            for idx in edited_df.index:

                prix = edited_df.at[
                    idx,
                    "Prix"
                ]

                prix_vente = edited_df.at[
                    idx,
                    "Prix de vente"
                ]

                if (
                    pd.isna(prix_vente)
                    or prix_vente == 0
                    or prix_vente == ""
                ):

                    edited_df.at[
                        idx,
                        "Prix de vente"
                    ] = calculer_prix_vente(
                        prix
                    )

            # -----------------------------------------------
            # Sauvegarde
            # -----------------------------------------------

            st.session_state.mes_commandes = (
                edited_df.copy()
            )

            sauvegarder()

            st.success(
                "✅ Toutes les modifications "
                "ont été enregistrées."
            )

            st.rerun()

    # --------------------------------------------------------
    # SUPPRESSION
    # --------------------------------------------------------

    lignes_a_supprimer = edited_df[
        edited_df["🗑️ Suppr."] == True
    ]

    with col_delete:

        if not lignes_a_supprimer.empty:

            if st.button(
                f"🗑️ Supprimer "
                f"{len(lignes_a_supprimer)} ligne(s)",
                type="secondary",
                width="stretch"
            ):

                ids_a_supprimer = (
                    lignes_a_supprimer["_id"]
                    .dropna()
                    .tolist()
                )

                try:
                    # Supprimer de Supabase uniquement les lignes sélectionnées
                    for identifiant in ids_a_supprimer:
                        (
                            supabase.table("commandes")
                            .delete()
                            .eq("id", int(identifiant))
                            .execute()
                        )

                    # Mettre à jour la mémoire locale après les suppressions
                    nouveau_df = edited_df[
                        edited_df["🗑️ Suppr."] == False
                    ].copy()

                    st.session_state.mes_commandes = nouveau_df

                    sauvegarder()

                    if "tableau_commandes" in st.session_state:
                        del st.session_state["tableau_commandes"]

                    st.success(
                        f"✅ {len(lignes_a_supprimer)} commande(s) supprimée(s)."
                    )

                    st.rerun()

                except Exception as e:
                    st.error(
                        "La suppression n'a pas pu être terminée. "
                        "Vérifie Supabase avant de réessayer."
                    )
                    st.error(str(e))

    # ========================================================
    # BILAN PAR CLIENT
    # ========================================================

    df_valide = edited_df.copy()

    st.markdown(
        "### 👥 Bilan par Client"
    )

    st.caption(
        "Montant total à facturer à chaque client "
        "(les colis refusés sont ignorés)."
    )

    df_clients_facturables = (
        df_valide[
            df_valide["Statut"]
            != "🚫 Colis refusé"
        ]
    )

    if not df_clients_facturables.empty:

        bilan_client = (
            df_clients_facturables
            .groupby(
                [
                    "Client",
                    "Téléphone",
                    "Pseudo",
                    "Type de pseudo"
                ]
            )
            .agg(
                Nombre_Articles=(
                    "Article / Lien",
                    "count"
                ),

                Total_DZ=(
                    "Prix de vente",
                    "sum"
                )
            )
            .reset_index()
        )

        st.dataframe(

            bilan_client,

            column_config={
                "_id": None,

                "Client":
                    st.column_config.TextColumn(
                        "Client"
                    ),

                "Téléphone":
                    st.column_config.TextColumn(
                        "Téléphone"
                    ),

                "Pseudo":
                    st.column_config.TextColumn(
                        "Pseudo"
                    ),

                "Type de pseudo":
                    st.column_config.TextColumn(
                        "Type de pseudo"
                    ),

                "Nombre_Articles":
                    st.column_config.NumberColumn(
                        "Nombre d'articles",
                        width="small"
                    ),

                "Total_DZ":
                    st.column_config.NumberColumn(
                        "Total à payer",
                        format="%.0f DZ",
                        width="medium"
                    )
            },

            hide_index=True,

            width="stretch"
        )

    else:

        st.info(
            "Aucune commande facturable "
            "pour le moment."
        )

    st.divider()

    # ========================================================
    # SOLDES
    # ========================================================

    depenses_shein = df_valide[
        df_valide["Vendeur"] == "SHEIN"
    ]

    st.markdown(
        "### 📊 Soldes Restants"
    )

    colonnes_soldes = st.columns(
        len(noms_comptes)
    )

    for i, nom_cpt in enumerate(
        noms_comptes
    ):

        ligne_compte = comptes_calculs[
            comptes_calculs[
                "Nom du compte"
            ] == nom_cpt
        ]

        montant_initial = (

            ligne_compte[
                "Montant initial (€)"
            ].values[0]

            if not ligne_compte.empty

            else 0.0
        )

        total_depense = (
            depenses_shein[
                depenses_shein["Compte"]
                == nom_cpt
            ]["Prix"]
            .sum()
        )

        total_depense = (

            float(total_depense)

            if pd.notna(
                total_depense
            )

            else 0.0
        )

        solde_restant = (
            montant_initial
            - total_depense
        )

        colonnes_soldes[i].metric(

            label=f"Solde : {nom_cpt}",

            value=f"{solde_restant:.2f} €",

            delta=f"-{total_depense:.2f} €",

            delta_color="inverse"
        )

    # ========================================================
    # MARGE
    # ========================================================

    st.markdown("---")

    total_ventes_dz = (
        df_valide[
            "Prix de vente"
        ].sum()
    )

    cout_achat_comptes = (
        comptes_calculs[
            "Coût d'achat"
        ].sum()
    )

    total_achats_marketplace = (
        df_valide[
            df_valide["Vendeur"]
            == "Marketplace"
        ]["Prix"]
        .sum()
    )

    cout_achat_total = (
        cout_achat_comptes
        + total_achats_marketplace
    )

    marge = (
        total_ventes_dz / 250
    ) - cout_achat_total

    colA, colB, colC = (
        st.columns(3)
    )

    colA.metric(

        label="Total des Ventes (DZ)",

        value=(
            f"{total_ventes_dz:,.0f} DZ"
            .replace(",", " ")
        )
    )

    colB.metric(

        label="Coût d'achat total (€)",

        value=f"{cout_achat_total:.2f} €",

        help=(
            "Somme des coûts d'achat "
            "des comptes + montant total "
            "des achats Marketplace"
        )
    )

    colC.metric(

        label="Marge Estimée (€)",

        value=f"{marge:.2f} €",

        help=(
            "Calcul : "
            "(Total Ventes DZ / 250) "
            "- Coût d'achat total"
        )
    )

else:

    st.info(
        "Votre liste est vide. "
        "Importez votre premier fichier HTML en haut !"
    )
