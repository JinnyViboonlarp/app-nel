# app-nel

A duplicate of [the CLAMS spaCy repository](https://github.com/clamsproject/app-spacy-nlp) that, instead of wrapping a spaCy NLP model, wraps an NEL (Named Entity Linking) pipeline that links named entities (as recognized by spaCy) with its wikidata's information (id, label, description, url).

After setting everything up according to [the original repository](https://github.com/clamsproject/app-spacy-nlp), the app could be tested by running the following command in your terminal

```bash
$ python app.py -t example-mmif-nel.json out-nel.json
```
