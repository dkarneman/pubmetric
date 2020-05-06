# pubmetric
PubMed authorship crawler - document the publishing record for trainees and their PIs.

## Setup
Clone this repo, and install the requirements.

To run this script, you'll want an API Key from Pubmed so that they don't throttle your
search.  Visit https://www.ncbi.nlm.nih.gov/account/settings/ to register one to your
email account. If you're performing a large number of searches, they recommend saving
that for after normal working hours or on weekends so that you don't bog down their
servers.

Add your email address and API Key to config.py file.

Then, locate your input file. It should be a CSV, and for now, it expects column names
and data broken as trainee Last Name and First Name, then the Thesis Mentor last name first:

```
| LastName | FirstName | ThesisMentor        |
|----------|-----------|---------------------|
| Potter   | Harry     | Dumbledore, Albus   |
| Granger  | Hermione  | McGonagall, Minerva |
| Malfoy   | Draco     | Riddle, Tom Marvolo |
```

Then, run the following command (with your own filepath, of course):
`python pubmetric.py '/Users/yourname/path/to/data/20191117input.csv'`

Enjoy!
