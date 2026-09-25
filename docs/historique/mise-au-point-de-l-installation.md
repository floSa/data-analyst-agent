# Mise au point de la procédure d'installation

Ce journal consigne les trois défauts trouvés en suivant la procédure d'installation depuis une machine vierge, en septembre 2026, et leur correction.

La procédure en vigueur : [INSTALLATION.md](../INSTALLATION.md).

Elle a été suivie pour de vrai, depuis un dossier vide, sur une machine où rien
n'existait. Trois choses ont manqué, toutes corrigées ; elles sont consignées
parce qu'elles disent où le terrain est glissant.

**1. `TMPDIR` fuyait du fichier d'environnement vers l'hôte.** Le fichier pose
`TMPDIR` sous le dossier de données — les CSV temporaires des tables SQL doivent
être montables dans le bac à sable, donc exister sur l'hôte. Mais `daactl`
*source* ce fichier, et le CLI `docker` qu'il appelle en héritait : le tout
premier `build`, avant que l'arborescence n'existe, échouait sur
`invalid output path: stat /var/lib/data-analyst-agent/tmp: no such file or
directory`. Les trois scripts oublient maintenant `TMPDIR` après lecture ; le
conteneur, lui, le reçoit par `env_file`.

**2. Un catalogue absent ne se voyait pas.** Le dossier de données neuf n'en a
pas ; l'application démarrait, la page s'ouvrait, et l'absence de toute source ne
se lisait que dans les journaux. `daactl start` le dit maintenant en clair et
renvoie à l'étape 5. C'est aussi pourquoi l'étape 4 (`daactl init`) existe : sans
elle, la seule façon de créer l'arborescence était de démarrer le service, donc
de le démarrer une première fois sans source.

**3. `daactl start` court-circuitait systemd.** L'unité activée mais jamais
démarrée *par* systemd, `daactl start` lançait bien l'application — et
`systemctl stop daa` ne l'arrêtait pas : systemd tenait l'unité pour inactive,
donc n'exécutait aucun `ExecStop`. Le service tournait, systemd le disait éteint.
Un exploitant qui coupe avant une sauvegarde aurait sauvegardé un service en
marche sans le savoir. `daactl start|stop|restart` délègue désormais à
`systemctl` dès que l'unité est installée.

Ce qui n'a **pas** manqué, et méritait de l'être : le conteneur a joint le moteur
et Postgres du premier coup (`host.docker.internal`), et le bac à sable a produit
sa figure au premier essai.
