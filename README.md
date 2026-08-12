# Outils de paramètres C++ (temporaire)

Ce dossier contient trois scripts Python, sans dépendance externe, pour travailler
sur les listes de paramètres multilignes dans les fichiers C++ et headers :

1. `align_cpp_parameters.py` aligne les paramètres et commentaires ;
2. `sync_cpp_comments.py` copie les commentaires depuis un fichier de référence ;
3. `capitalize_cpp_parameters.py` met en majuscule le premier caractère des noms
   de paramètres.

Les scripts et `cpp_signature_tools.py` doivent rester ensemble dans ce dossier.

## 1. Aligner les paramètres

L'outil :

- indente chaque paramètre d'une tabulation par rapport à la fonction ;
- aligne les noms une tabulation après le type le plus long ;
- aligne les `=` des valeurs par défaut dans les headers ;
- aligne les commentaires de fin de ligne une tabulation après la ligne de code
  la plus longue ;
- permet de choisir entre les virgules à la fin du paramètre précédent (mode par
  défaut) et les virgules au début de la ligne suivante ;
- n'utilise que des caractères de tabulation pour les espacements d'alignement ;
- conserve les fins de ligne (`LF` ou `CRLF`) et un éventuel BOM UTF-8 ;
- peut être relancé sans modifier une seconde fois les fichiers (idempotence).

### Utilisation

Le chemin peut désigner un fichier ou un dossier. Les sous-dossiers sont parcourus
par défaut.

```bash
python3 align_cpp_parameters.py /chemin/vers/les/sources
```

Avant d'écrire, il est recommandé de visualiser le résultat :

```bash
python3 align_cpp_parameters.py --diff /chemin/vers/les/sources
```

Pour un contrôle sans écriture (code retour `1` si un changement est nécessaire) :

```bash
python3 align_cpp_parameters.py --check /chemin/vers/les/sources
```

Options utiles :

```text
--tab-width 4             largeur visuelle des tabulations
--comma-style trailing    virgule après le paramètre (mode par défaut)
--comma-style leading     virgule au début de la ligne suivante
--extensions .cpp,.h      extensions à traiter
--no-recursive            ne pas parcourir les sous-dossiers
--verbose                 afficher les fichiers inchangés
```

Pour placer les virgules au début des lignes :

```bash
python3 align_cpp_parameters.py --comma-style leading /chemin/vers/les/sources
```

Dans ce mode, la virgule est placée après la tabulation d'indentation, suivie
d'un espace. Les noms, valeurs par défaut et commentaires restent alignés :

```cpp
Result
make_order(
	int								id		= 0		// identifiant
	, const std::vector<std::string>&	symbols	= {}		// symboles
	, bool							enabled	= true	// état
);
```

### Exemple

Entrée :

```cpp
Result
make_order(
    int id = 0 // identifiant
    , const std::vector<std::string>& symbols = {} // symboles
    , bool enabled = true // état
);
```

Sortie (les grands espacements visibles sont des tabulations) :

```cpp
Result
make_order(
	int								id		= 0,	// identifiant
	const std::vector<std::string>&	symbols	= {},	// symboles
	bool							enabled	= true	// état
);
```

### Périmètre de sécurité

Le formateur travaille uniquement sur les signatures multilignes où la parenthèse
ouvrante termine la ligne, la parenthèse fermante commence une autre ligne, et
chaque paramètre tient sur une ligne. Il ignore les appels détectés à l'intérieur
des corps de fonctions, les directives préprocesseur et les déclarateurs qu'il ne
reconnaît pas. Les commentaires de documentation situés avant la fonction et le
type de retour restent inchangés.

Pour les syntaxes C++ très complexes ou générées par macros, utiliser d'abord
`--diff` et relire le résultat.

## 2. Copier les commentaires d'un fichier de référence

Le premier chemin est le `.cpp` ou header de référence. Le second est le fichier
à modifier :

```bash
python3 sync_cpp_comments.py reference.h cible.cpp
```

Prévisualisation et contrôle :

```bash
python3 sync_cpp_comments.py --diff reference.h cible.cpp
python3 sync_cpp_comments.py --check reference.h cible.cpp
python3 sync_cpp_comments.py --strict reference.h cible.cpp
```

Les fonctions sont associées uniquement à partir de leur nom et de leur nombre
de paramètres. La portée et les types des paramètres sont ignorés. Dans le
fichier cible, le préfixe `FIC_` placé au début du nom est retiré pour effectuer
l'association : `FIC_build` correspond donc à `build`.

Si plusieurs fonctions de référence ont le même nom et le même nombre de
paramètres, l'association est ambiguë : elle est ignorée et signalée au lieu de
choisir une surcharge à partir de sa portée ou de ses types.

Le placement des virgules est sans effet sur la synchronisation : le fichier de
référence et le fichier cible peuvent employer des styles différents.

Le script copie :

- le bloc de commentaires placé immédiatement avant le nom de la fonction ;
- le commentaire situé après la ligne du nom de fonction ;
- les commentaires de fin de ligne des paramètres, associés d'abord par nom puis
  par position ; les types ne participent jamais à cette association ;
- le commentaire situé après la parenthèse fermante.

La tabulation obligatoire concerne uniquement les commentaires qui occupent une
ligne à eux seuls. Ceux qui sont collés à gauche ou précédés uniquement
d'espaces sont ignorés. Un bloc placé avant une fonction est copié uniquement si
chacune de ses lignes respecte cette règle. Les commentaires placés après du
code — notamment après un paramètre — sont copiés même s'ils sont séparés du code
uniquement par des espaces.

Un commentaire cible est remplacé lorsque la référence en fournit un. Si la
référence n'a pas de commentaire à cet emplacement, le commentaire cible est
conservé. Une association ambiguë est ignorée et signalée ; `--strict` transforme
ce cas en erreur.

## 3. Mettre les noms de paramètres en majuscule

Le chemin peut désigner un fichier ou un dossier. Les sous-dossiers sont parcourus
par défaut :

```bash
python3 capitalize_cpp_parameters.py /chemin/vers/les/sources
```

Prévisualisation et contrôle :

```bash
python3 capitalize_cpp_parameters.py --diff /chemin/vers/les/sources
python3 capitalize_cpp_parameters.py --check /chemin/vers/les/sources
python3 capitalize_cpp_parameters.py --strict /chemin/vers/les/sources
```

Exemples de renommage : `value` devient `Value` et `item_count` devient
`Item_count`. Un nom commençant déjà par une majuscule ou par `_` reste inchangé.

Pour une définition en `.cpp` ou une fonction inline, le script renomme également
les usages dans la liste d'initialisation et le corps. Il ne modifie pas les
chaînes, commentaires, accès `this->value`, `objet.value` ou noms qualifiés. Si le
renommage créait deux paramètres identiques, la fonction est ignorée et signalée.

Comme l'aligneur, ces deux scripts traitent volontairement les signatures
multilignes avec la parenthèse ouvrante en fin de ligne, la parenthèse fermante sur
une autre ligne et un paramètre par ligne. Utiliser `--diff` avant une modification
de masse.

Ordre conseillé pour combiner les outils : synchroniser les commentaires,
capitaliser les paramètres, puis lancer l'aligneur en dernier.

## Tests

Depuis ce dossier :

```bash
python3 -m unittest discover -s tests -v
```
