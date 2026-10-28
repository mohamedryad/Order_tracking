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
        with open(".streamlit/secrets.toml", "rb") as f:
            secrets = tomli.load(f)

        url = secrets["SUPABASE_URL"]
        cle = secrets["SUPABASE_KEY"]

    return create_client(url, cle)

supabase = obtenir_supabase()

st.title("🛍 Mon Suivi de Commandes Shein (Multi-Comptes & CRM)")

FICHIER_SAUVEGARDE = "sauvegarde_shein.json"


# ============================================================
# SYSTÈME DE SAUVEGARDE JSON / SUPABASE
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
            "Aucune donnée locale ne sera initialisée automatiquement."
        )
        st.error(str(e))
        st.stop()

def recharger_donnees():
    df_comptes, df_commandes = charger_sauvegarde()
    st.session_state.comptes = df_comptes
    st.session_state.mes_commandes = df_commandes

    if "tableau_commandes" in st.session_state:
        del st.session_state["tableau_commandes"]
    if "tableau_comptes" in st.session_state:
        del st.session_state["tableau_comptes"]

def sauvegarder_table_supabase(nom_table, df):
    if "_id" not in df.columns:
        raise ValueError("Identifiants manquants : sauvegarde annulée.")

    ids = df["_id"].dropna().tolist()
    if len(ids) != len(set(ids)):
        raise ValueError("Identifiants en double : sauvegarde annulée.")

    for idx, ligne in df.iterrows():
        donnees = ligne.to_dict()
        identifiant = donnees.pop("_id", None)

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
            supabase.table(nom_table).update({"donnees": donnees}).eq("id", identifiant).execute()
        else:
            resultat = supabase.table(nom_table).insert({"donnees": donnees}).execute()
            if not resultat.data:
                raise RuntimeError(f"Échec de création dans {nom_table}.")
            df.at[idx, "_id"] = resultat.data[0]["id"]

def sauvegarder():
    try:
        sauvegarder_table_supabase("comptes", st.session_state.comptes)
        sauvegarder_table_supabase("commandes", st.session_state.mes_commandes)
        st.session_state.comptes = st.session_state.comptes.copy()
        st.session_state.mes_commandes = st.session_state.mes_commandes.copy()
        return True
    except Exception as e:
        st.error("Échec de la sauvegarde Supabase. Vérifie ta connexion et les données.")
        st.error(str(e))
        return False


# ============================================================
# INITIALISATION DE LA MÉMOIRE
# ============================================================

if "comptes" not in st.session_state or "mes_commandes" not in st.session_state:
    df_comptes, df_commandes = charger_sauvegarde()

    if df_comptes is not None and not df_comptes.empty:
        st.session_state.comptes = df_comptes
    else:
        st.session_state.comptes = pd.DataFrame({
            "Nom du compte": ["Compte A"],
            "Montant initial (€)": [100.0],
            "Coût d'achat": [0.0]
        })

    if df_commandes is not None and not df_commandes.empty:
        st.session_state.mes_commandes = df_commandes
    else:
        st.session_state.mes_commandes = pd.DataFrame(
            columns=[
                "_id", "🗑️ Suppr.", "Client", "Téléphone", "Pseudo",
                "Type de pseudo", "Article / Lien", "SKU", "Compte",
                "Vendeur", "Prix", "Prix de vente", "⚠️ Vérifier prix", "Statut"
            ]
        )


# ============================================================
# FONCTIONS D'EXTRACTION HTML
# ============================================================

def extraire_infos_html(html_content):
    soup = BeautifulSoup(html_content, "html.parser")

    # Prix
    prix_trouve = "Prix introuvable"
    tag_prix = soup.find(id="productMainPriceId")
    if not tag_prix:
        tag_prix = soup.find(class_=lambda c: c and "productPrice__main" in c)

    if tag_prix:
        if tag_prix.has_attr("aria-label"):
            prix_brut = tag_prix["aria-label"].strip()
        else:
            prix_brut = tag_prix.get_text(strip=True)

        match = re.search(r"\d+[.,]\d+\s*€?", prix_brut)
        if match:
            prix_trouve = match.group(0).replace(" ", "")
            if not prix_trouve.endswith("€"):
                prix_trouve += "€"

    if prix_trouve == "Prix introuvable" or not prix_trouve:
        match_json = re.search(r'"salePrice"\s*:\s*\{.*?"amountWithSymbol"\s*:\s*"([^"]+)"', html_content)
        if match_json:
            prix = match_json.group(1)
            prix_trouve = prix if "€" in prix else f"{prix}€"
        else:
            match_montant = re.search(r'"salePrice"\s*:\s*\{.*?"amount"\s*:\s*"([\d\.]+)"', html_content)
            if match_montant:
                prix_trouve = f"{match_montant.group(1).replace('.', ',')}€"

    if prix_trouve == "Prix introuvable" or not prix_trouve:
        textes_prix = soup.find_all(string=re.compile("€"))
        for texte in textes_prix:
            texte_propre = texte.strip().lower()
            if "dès" in texte_propre or "livraison" in texte_propre or "code" in texte_propre:
                continue
            match = re.search(r"\d+[.,]\d+\s*€", texte_propre)
            if match:
                prix_trouve = match.group(0).replace(" ", "")
                break

    prix_float = 0.0
    if prix_trouve and prix_trouve != "Prix introuvable":
        try:
            prix_nettoye = re.sub(r"[^\d,.]", "", prix_trouve).replace(",", ".")
            prix_float = float(prix_nettoye)
        except ValueError:
            prix_float = 0.0

    # SKU
    sku_trouve = "N/A"
    match_sku = re.search(r"SKU:\s*([a-zA-Z0-9]+)", html_content, re.IGNORECASE)
    if match_sku:
        sku_trouve = match_sku.group(1)
    else:
        match_json_sku = re.search(r'"goods_sn"\s*:\s*"([a-zA-Z0-9]+)"', html_content)
        if match_json_sku:
            sku_trouve = match_json_sku.group(1)
        else:
            match_json_sku2 = re.search(r'"sku"\s*:\s*"([a-zA-Z0-9]+)"', html_content, re.IGNORECASE)
            if match_json_sku2:
                sku_trouve = match_json_sku2.group(1)

    # Vendeur
    vendeur_trouve = "SHEIN"
    tag_marche = soup.find(class_=lambda c: c and "soldbybox-header__corner-text" in c)
    if tag_marche and "marché" in tag_marche.get_text(strip=True).lower():
        vendeur_trouve = "Marketplace"
    else:
        tag_vendeur = soup.find(class_=lambda c: c and "soldbybox-header__title-text" in c)
        if tag_vendeur:
            texte_vendeur = tag_vendeur.get_text(strip=True)
            if ":" in texte_vendeur:
                nom_vendeur = texte_vendeur.split(":", 1)[1].strip()
                if nom_vendeur.upper() != "SHEIN":
                    vendeur_trouve = "Marketplace"

    return prix_float, sku_trouve, vendeur_trouve


# ============================================================
# COULEUR DES LIGNES
# ============================================================

def coloriser_ligne(row):
    val = row["Statut"]
    couleur = ""
    if val in ["🛒 Commandé", "📦 En préparation"]:
        couleur = "background-color: #D3D3D3;"
    elif val in ["✈️ Expédié", "🚚 En cours de livraison"]:
        couleur = "background-color: #FFD580;"
    elif val == "✅ Livré":
        couleur = "background-color: #90EE90;"
    elif val == "🚫 Colis refusé":
        return ["background-color: #FF7F7F;"] * len(row)
    return [couleur] * len(row)


# ============================================================
# TRI ET CALCULS
# ============================================================

def trier_commandes(df):
    ordre_statut = {
        "🚚 En cours de livraison": 0,
        "✈️ Expédié": 1,
        "📦 En préparation": 2,
        "🛒 Commandé": 3,
        "✅ Livré": 4,
        "🚫 Colis refusé": 5
    }
    df = df.copy()
    df["_ordre_statut"] = df["Statut"].map(ordre_statut).fillna(99)
    df = df.sort_values(by="_ordre_statut", ascending=True, kind="stable")
    df = df.drop(columns=["_ordre_statut"]).reset_index(drop=True)
    return df

def calculer_prix_vente(prix_achat):
    try:
        p = float(prix_achat)
        if p < 4.0:
            return (p + 2.0) * 320
        else:
            return (p + 4.0) * 320
    except (ValueError, TypeError):
        return 0.0

if st.button("🔄 Recharger les données depuis Supabase"):
    recharger_donnees()
    st.rerun()


# ============================================================
# GESTION DES COMPTES
# ============================================================

st.markdown("### 💳 Gestion des Comptes")
st.caption("Ajoutez vos différents comptes ici. Ces informations serviront à déduire les achats et calculer la marge.")

edited_comptes = st.data_editor(
    st.session_state.comptes[["_id", "Nom du compte", "Montant initial (€)", "Coût d'achat"]],
    column_config={
        "_id": None,
        "Nom du compte": st.column_config.TextColumn("Nom du compte", required=True),
        "Montant initial (€)": st.column_config.NumberColumn("Montant initial (€)", format="%.2f €", min_value=0.0),
        "Coût d'achat": st.column_config.NumberColumn("Coût d'achat", help="Ce que vous a coûté ce compte", format="%.2f")
    },
    num_rows="dynamic",
    hide_index=True,
    width="stretch"
)

if not edited_comptes.equals(st.session_state.comptes):
    st.session_state.comptes = edited_comptes
    sauvegarder()

noms_comptes = edited_comptes["Nom du compte"].dropna().astype(str).tolist()
noms_comptes = [nom for nom in noms_comptes if nom.strip() != ""]
if not noms_comptes:
    noms_comptes = ["Aucun compte"]

st.divider()


# ============================================================
# AJOUT D'ARTICLES
# ============================================================

st.markdown("### ➕ Nouvelle Commande Client")

with st.form("formulaire_ajout", clear_on_submit=True):
    st.markdown("#### 👤 Informations du client")
    col_nom, col_prenom, col_tel, col_type_pseudo, col_pseudo = st.columns(5)

    with col_nom:
        client_nom = st.text_input("Nom")
    with col_prenom:
        client_prenom = st.text_input("Prénom")
    with col_tel:
        client_tel = st.text_input("Téléphone", placeholder="0555...")
    with col_type_pseudo:
        type_pseudo = st.selectbox("Type de pseudo", ["Instagram", "Facebook", "WhatsApp"], index=0)
    with col_pseudo:
        client_pseudo = st.text_input("Pseudo (Optionnel)", placeholder="@...")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("#### 🛍️ Articles du client")
    col1, col2 = st.columns([4, 1])

    with col1:
        fichiers_html = st.file_uploader("1. Glissez le(s) fichier(s) HTML ici", type=["html", "htm"], accept_multiple_files=True)
    with col2:
        url_input = st.text_input("2. Lien (Optionnel) :", placeholder="https://...")
        st.markdown("<br>", unsafe_allow_html=True)
        bouton_ajouter = st.form_submit_button("Ajouter à la liste", width="stretch", type="primary")

if bouton_ajouter:
    if fichiers_html:
        nouvelles_commandes = []
        compte_par_defaut = noms_comptes[0]
        nom_complet = f"{client_nom} {client_prenom}".strip()
        if not nom_complet:
            nom_complet = "Client Inconnu"

        for fichier in fichiers_html:
            html_source = fichier.getvalue().decode("utf-8", errors="ignore")
            prix, sku, vendeur = extraire_infos_html(html_source)
            nom_article = url_input if (len(fichiers_html) == 1 and url_input) else fichier.name

            nouvelles_commandes.append({
                "_id": None,
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
                "Prix de vente": calculer_prix_vente(prix),
                "⚠️ Vérifier prix": False,
                "Statut": "🛒 Commandé"
            })

        df_nouveaux = pd.DataFrame(nouvelles_commandes)
        st.session_state.mes_commandes = pd.concat([st.session_state.mes_commandes, df_nouveaux], ignore_index=True)
        sauvegarder()
        st.success(f"{len(fichiers_html)} article(s) ajouté(s) pour {nom_complet} avec succès !")
    else:
        st.warning("Veuillez d'abord glisser au moins un fichier HTML.")

st.divider()


# ============================================================
# LISTE DES COMMANDES
# ============================================================

st.markdown("### 📦 Ma Liste de Commandes")
st.caption("Vous pouvez modifier plusieurs cellules et plusieurs lignes. Cliquez ensuite sur 💾 Enregistrer les modifications.")

if not st.session_state.mes_commandes.empty:
    df = trier_commandes(st.session_state.mes_commandes)

    if "⚠️ Vérifier prix" not in df.columns:
        df["⚠️ Vérifier prix"] = False

    if "Prix de vente" not in df.columns:
        df["Prix de vente"] = df["Prix"].apply(calculer_prix_vente)

    commandes_a_verifier = df[df["⚠️ Vérifier prix"] == True]
    if not commandes_a_verifier.empty:
        st.warning(f"⚠️ **{len(commandes_a_verifier)} article(s) ont un prix de vente à vérifier !** Pensez à contrôler le prix avant de valider la commande.")

    df_colorise = df.style.apply(coloriser_ligne, axis=1)

    edited_df = st.data_editor(
        df_colorise,
        key="tableau_commandes",
        column_order=[
            "🗑️ Suppr.", "Client", "Téléphone", "Pseudo", "Type de pseudo",
            "SKU", "Compte", "Vendeur", "Prix", "Prix de vente",
            "⚠️ Vérifier prix", "Statut", "Article / Lien"
        ],
        column_config={
            "_id": None,
            "🗑️ Suppr.": st.column_config.CheckboxColumn("Suppr.", width=70),
            "Client": st.column_config.TextColumn("Client", width="medium"),
            "Téléphone": st.column_config.TextColumn("Téléphone", width="medium"),
            "Pseudo": st.column_config.TextColumn("Pseudo", width="medium"),
            "Type de pseudo": st.column_config.SelectboxColumn("Type de pseudo", options=["Instagram", "Facebook", "WhatsApp", "Inconnu"], width="small"),
            "Article / Lien": st.column_config.LinkColumn("Article / Lien", width="large"),
            "SKU": st.column_config.TextColumn("SKU", width=80),
            "Compte": st.column_config.SelectboxColumn("Payé par", options=noms_comptes, required=True, width="small"),
            "Vendeur": st.column_config.TextColumn("Vendeur", width="small"),
            "Prix": st.column_config.NumberColumn("Prix d'achat", format="%.2f €", width=90),
            "Prix de vente": st.column_config.NumberColumn("Prix de vente", format="%.0f DZ", width=100),
            "⚠️ Vérifier prix": st.column_config.CheckboxColumn("⚠️ Vérifier", help="Cochez cette case si vous n'êtes pas sûr du prix de vente.", width=80),
            "Statut": st.column_config.SelectboxColumn("Statut", options=["🛒 Commandé", "📦 En préparation", "✈️ Expédié", "🚚 En cours de livraison", "✅ Livré", "🚫 Colis refusé"], required=True, width="medium")
        },
        hide_index=True,
        width="stretch",
        num_rows="fixed"
    )

    col_save, col_delete, col_reset = st.columns([2, 2, 2])

    with col_save:
        if st.button("💾 Enregistrer les modifications", type="primary", width="stretch"):
            for idx in edited_df.index:
                prix = edited_df.at[idx, "Prix"]
                prix_vente = edited_df.at[idx, "Prix de vente"]
                if pd.isna(prix_vente) or prix_vente == 0 or prix_vente == "":
                    edited_df.at[idx, "Prix de vente"] = calculer_prix_vente(prix)

            st.session_state.mes_commandes = edited_df.copy()
            sauvegarder()
            st.success("✅ Toutes les modifications ont été enregistrées.")
            st.rerun()

    lignes_a_supprimer = edited_df[edited_df["🗑️ Suppr."] == True]
    with col_delete:
        if not lignes_a_supprimer.empty:
            if st.button(f"🗑️ Supprimer {len(lignes_a_supprimer)} ligne(s)", type="secondary", width="stretch"):
                ids_a_supprimer = lignes_a_supprimer["_id"].dropna().tolist()
                try:
                    for identifiant in ids_a_supprimer:
                        supabase.table("commandes").delete().eq("id", int(identifiant)).execute()

                    nouveau_df = edited_df[edited_df["🗑️ Suppr."] == False].copy()
                    st.session_state.mes_commandes = nouveau_df
                    sauvegarder()

                    if "tableau_commandes" in st.session_state:
                        del st.session_state["tableau_commandes"]

                    st.success(f"✅ {len(lignes_a_supprimer)} commande(s) supprimée(s).")
                    st.rerun()
                except Exception as e:
                    st.error("La suppression n'a pas pu être terminée. Vérifie Supabase avant de réessayer.")
                    st.error(str(e))

    st.divider()

    # ========================================================
    # 📊 BÉNÉFICES & BILAN FINANCIER (LIVRÉS UNIQUEMENT)
    # ========================================================

    st.markdown("### 📊 Bilan Financier (Commandes Livrées Uniquement)")
    st.caption("Ce calcul prend en compte **exclusivement** les articles avec le statut **✅ Livré**.")

    # Filtrer uniquement les articles livrés
    df_livres = edited_df[edited_df["Statut"] == "✅ Livré"].copy()

    if not df_livres.empty:
        # Nettoyage et conversion numérique sécurisée
        df_livres["Prix_Achat_Num"] = pd.to_numeric(df_livres["Prix"], errors="coerce").fillna(0.0)
        df_livres["Prix_Vente_Num"] = pd.to_numeric(df_livres["Prix de vente"], errors="coerce").fillna(0.0)

        # Totaux
        total_articles_livres = len(df_livres)
        cout_achat_total_eur = df_livres["Prix_Achat_Num"].sum()
        chiffre_affaires_dz = df_livres["Prix_Vente_Num"].sum()

        # Hypothèse de calcul du bénéfice en DZ :
        # Conversion du coût d'achat au taux moyen implicite de la formule (1 € ~ 320 DZ)
        cout_achat_total_dz = cout_achat_total_eur * 320
        benefice_total_dz = chiffre_affaires_dz - cout_achat_total_dz

        # Affichage des métriques financières
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("📦 Articles Livrés", f"{total_articles_livres} art.")
        m2.metric("💶 Coût d'achat total", f"{cout_achat_total_eur:.2f} €")
        m3.metric("💵 Total Ventes (Recettes)", f"{chiffre_affaires_dz:,.0f} DZ")
        m4.metric("📈 Bénéfice Net (Est. DZ)", f"{benefice_total_dz:,.0f} DZ")

    else:
        st.info("Aucun article n'est actuellement marqué comme **✅ Livré**.")

    st.divider()

    # ========================================================
    # BILAN PAR CLIENT
    # ========================================================

    st.markdown("### 👥 Bilan par Client")
    st.caption("Affiche les totaux et bénéfices par client sur les articles livrés uniquement.")

    if not df_livres.empty:
        # Calcul du bénéfice par ligne
        df_livres["Benefice_Ligne_DZ"] = df_livres["Prix_Vente_Num"] - (df_livres["Prix_Achat_Num"] * 320)

        bilan_client = (
            df_livres
            .groupby(["Client", "Téléphone", "Pseudo", "Type de pseudo"])
            .agg(
                Articles_Livres=("Article / Lien", "count"),
                Total_Paye_DZ=("Prix_Vente_Num", "sum"),
                Benefice_DZ=("Benefice_Ligne_DZ", "sum")
            )
            .reset_index()
        )

        st.dataframe(
            bilan_client,
            column_config={
                "Client": st.column_config.TextColumn("Client"),
                "Téléphone": st.column_config.TextColumn("Téléphone"),
                "Pseudo": st.column_config.TextColumn("Pseudo"),
                "Type de pseudo": st.column_config.TextColumn("Type de pseudo"),
                "Articles_Livres": st.column_config.NumberColumn("Articles Livrés", format="%d"),
                "Total_Paye_DZ": st.column_config.NumberColumn("Total encaissé", format="%.0f DZ"),
                "Benefice_DZ": st.column_config.NumberColumn("Bénéfice réalisé", format="%.0f DZ")
            },
            hide_index=True,
            width="stretch"
        )
    else:
        st.info("Aucune donnée client disponible pour des articles livrés.")