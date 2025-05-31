Todo List Détaillee pour NewApp
1. Connexion (Compte ERPNext)
Page de Login

Formulaire d'authentification (email/mot de passe)

Validation des champs avant envoi

Gestion des erreurs (compte invalide, mot de passe incorrect)

Redirection vers le dashboard après connexion

Sécurité

Stockage sécurisé du token JWT (ERPNext)

Gestion des sessions (timeout après inactivité)

Protection contre les attaques (brute force, CSRF)

2. Import Fichier CSV
Upload du Fichier

Interface drag & drop ou sélection manuelle

Restrictions (format .csv uniquement, taille max 10MB)

Prévisualisation avant Import

Affichage des 5 premières lignes pour vérification

Détection automatique des colonnes

Validation des Données

Format des dates (erreur si JJ/MM/AAAA invalide)

Champs obligatoires (ID employé, nom, salaire, etc.)

Vérification des doublons (empêcher les imports en double)

Confirmation & Import Final

Bouton "Confirmer l’import" après validation

Message de succès/échec avec détails des erreurs

Journalisation (qui a importé, quand, combien de lignes)

3. Gestion des Employés
Liste des Employés (Filtres & Recherche)

Tableau paginé avec colonnes (ID, Nom, Département, Poste)

Filtres dynamiques (département, statut, date d’embauche)

Barre de recherche (nom, ID)

Fiche Employé

Affichage détaillé (coordonnées, contrat, historique)

Liste des salaires par mois (liens vers fiches de paie)

Bouton "Exporter en PDF" (fiche synthétique)

4. Gestion des Salaires
Tableau des Salaires (Filtre par Mois)

Vue tabulaire (employés × éléments de salaire)

Calcul automatique des totaux (brut/net)

Filtre mois/année (sélection via calendrier)

Fiche de Paie (Export PDF)

Modèle professionnel (en-tête entreprise, logo)

Détails (salaire brut, cotisations, net à payer)

Option "Télécharger" ou "Envoyer par email"

5. Synchronisation avec ExistingApp (ERPNext)
API d’Import

Envoi des nouveaux employés vers ERPNext après validation

Mise à jour des fiches existantes (si modifications)

Gestion des Erreurs

Retry automatique en cas d’échec API

Log des erreurs (dashboard admin)

6. Bonus (Améliorations Futures)
Notifications

Alertes email pour les imports réussis/échoués

Historique des Imports

Consultation des anciens fichiers CSV

Statistiques (nombre d’employés ajoutés/mis à jour)

Multi-utilisateurs & Rôles

Restrictions (RH = accès complet, Manager = lecture seule)

Priorisation
Login + Sécurité → Import CSV → Liste Employés → Fiches Salaires

Export PDF → Tableau Salaires → Synchronisation ERPNext

Améliorations (Notifications, Historique, Rôles)

Cette structure permet un développement itératif avec des livrables fonctionnels à chaque étape.