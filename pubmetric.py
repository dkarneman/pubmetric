import requests
import re
import copy
import click
import unicodedata
import pandas as pd
import datetime as dt
import xml.etree.ElementTree as et
from config import TOO_MANY_PAPERS, WITH_INITIAL, NCBI_EMAIL, NCBI_API_KEY

# Contstants, URLs, and search tags
ATAG = "[Author] "
LTAG = "[ad] "
HOST = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
ESEARCH = "{}esearch.fcgi".format(HOST)
ESUMMARY = "{}esummary.fcgi".format(HOST)
EFETCH = "{}efetch.fcgi".format(HOST)

DEFAULT_FETCH_PARAMS = {
    'email': NCBI_EMAIL,
    'api_key': NCBI_API_KEY,
    'db': 'pubmed',
    'retmode': 'json',
}

EMPTY_TRAINEE_STATS = {
    # empty dict to hold the information about each trainee
    'research_papers': 0,  # Placeholder to count Research Papers (not reviews)
    'first_author_research_papers': 0,
    'first_author_journals': [],  # empty list for journal titles
    'reviews': 0,
    'first_author_reviews': 0,
    }


def format_name(last, first, with_initial=True):
    """Standardize name format, and optionally, use the first initial

    >>> format_name('Joyce', 'James', with_initial=False)
    'Joyce'
    >>> format_name('Joyce', 'James', with_initial=True)
    'Joyce J'
    >>> format_name('Joyce   ', 'James', with_initial=True)
    'Joyce J'
    """
    if with_initial:
        return last.strip() + ' ' + first.strip()[0]
    else:
        return last.strip()


def strip_accents(text):
    """Strip accents from input String. This makes comparing user inupt
    (which often doesn't contain proper accent marks) with the names
    returned from PubMed, which often do contain accents and diacriticals.

    >>> strip_accents('Montréal')
    'Montreal'
    >>> strip_accents('über')
    'uber'
    >>> strip_accents('Françoise')
    'Francoise'
    """

    try:
        text = unicode(text, 'utf-8')
    except (TypeError, NameError):  # unicode is a default on python 3
        pass
    text = unicodedata.normalize('NFD', text)
    text = text.encode('ascii', 'ignore')
    text = text.decode("utf-8")
    return str(text)


def flatten_name(name):
    """ Given a name, transform it to decode accented characters and remove
    non-letters so that the spreadsheet can be compared with the Pubmed result

    >>> flatten_name('James  Joyce   ')
    'jamesjoyce'
    >>> flatten_name('Saint-Exupéry')
    'saintexupery'
    >>> flatten_name("O'Neill E")
    'oneille'
    >>> flatten_name('le Carré')
    'lecarre'
    """
    name = strip_accents(name.strip().lower())
    name = re.sub('[^0-9a-zA-Z]', '', name)
    return name


def names_match(name1, name2):
    """Standardize names and determine whether they match.  Returns True or False

    >>> names_match('Joyce James','  Joyce   James ')
    True
    >>> names_match('T. S. Eliot','T S Eliot')
    True
    >>> names_match('José Saramago','Jose Saramago')
    True
    >>> names_match('John Ronald Reuel Tolkien','J. R. R. Tolkein')
    False

    """
    return flatten_name(name1) == flatten_name(name2)


def extract_identifier(articleIds, id_type):
    for aid in articleIds:
        if id_type == aid['idtype']:
            return aid
    else:
        return None


class AuthorSearch:
    def __init__(self, row, with_initial=WITH_INITIAL):
        """Initialize this search session with terms"""
        print(row)
        self.trainee_stats = copy.deepcopy(EMPTY_TRAINEE_STATS)
        assert self.trainee_stats['first_author_journals'] == []
        self.abstracts = {}
        self.summaries = {}
        self.paper_content = []
        self._parse_trainee_mentor(row, with_initial)

    def _parse_trainee_mentor(self, row, with_initial):
        """String format the trainee, mentor, and location"""
        self.trainee = format_name(row.LastName, row.FirstName, with_initial)
        mentor = row.ThesisMentor
        try:
            mentor = mentor.split(',')
            if len(mentor) > 1:
                mentor = format_name(mentor[0], mentor[1], with_initial)
            else:
                mentor = mentor[0]
        except Exception:
            mentor = None
        try:
            if pd.isna(row.Location):
                location = None
            else:
                location = row.Location
        except Exception:
            location = None

        self.mentor = mentor
        self.location = location

    def _params_with_history(self):
        params = DEFAULT_FETCH_PARAMS.copy()
        params['WebEnv'] = self.search_results['webenv']
        params['query_key'] = self.search_results['querykey']
        return params

    def search(self):
        """Retrieve the PubMed search results given an author and mentor
        If no results are found, it returns None"""
        search_term = self.trainee + ATAG
        if self.mentor:
            search_term += self.mentor + ATAG
        if self.location:
            search_term += self.location + LTAG
        print("search term: {}".format(search_term))

        params = DEFAULT_FETCH_PARAMS.copy()
        params['usehistory'] = 'y'  # This caches results on the NCBI server
        params['term'] = search_term
        resp = requests.get(ESEARCH, params=params)
        result = resp.json()

        if result['header']['version'] != '0.3':
            print("""Warning: the ESearch version has changed.
            Results may be incorrect""")

        self.search_results = result['esearchresult']
        return result

    def fetch_summaries(self):
        """Use the search results to download a the paper summaries.
        This method passes the WebEnv and query_key to fetch all of the
        results at once. Individual papers can be extracted from the response
        """
        params = self._params_with_history()
        resp = requests.get(ESUMMARY, params=params)
        summaries_json = resp.json()['result']
        self.pmids = summaries_json['uids']
        for pmid in self.pmids:
            self.summaries[pmid] = PaperSummary(pmid, summaries_json[pmid])

    def fetch_abstracts(self):
        """Use the search results to download a the full XML record.
        This method passes the WebEnv and query_key to fetch all of the
        results at once. Individual papers can be extracted from the response
        """
        params = self._params_with_history()
        params['retmode'] = 'XML'
        resp = requests.get(EFETCH, params=params)
        # Store an XML Element Tree
        abstracts_xml = et.fromstring(resp.text)
        for abstract in abstracts_xml.findall('.//MedlineCitation'):
            pmid = abstract.find('.//PMID').text
            self.abstracts[pmid] = PaperAbstract(pmid, abstract)

    def assess_trainee(self):
        self.search()

        # How many total papers did they publish?
        self.trainee_stats['paper_count'] = self.search_results['count']
        # add the list of papers to the trainee stats dict
        self.trainee_stats['pmids'] = self.search_results['idlist']

        # If no papers were found in the search, stop here
        if int(self.trainee_stats['paper_count']) == 0:
            self.trainee_stats['error'] = 'Search returned zero results'
            return self.trainee_stats

        # If a large number of papers were found, the search terms weren't
        # specific enough. Don't attempt further processing
        if int(self.trainee_stats['paper_count']) > TOO_MANY_PAPERS:
            self.trainee_stats['error'] = 'Search returned too many results'
            return self.trainee_stats

        self.fetch_summaries()
        self.fetch_abstracts()

        for pmid in self.pmids:
            this_summary = self.summaries[pmid]
            this_abstract = self.abstracts[pmid]
            first_author = this_summary.is_first_author(self.trainee) or \
                this_abstract.is_first_author(self.trainee)
            if this_summary.is_review():  # if this is a review
                self.trainee_stats['reviews'] += 1
                if first_author:
                    self.trainee_stats['first_author_reviews'] += 1
            else:  # if it's not a review, it must be a research paper
                self.trainee_stats['research_papers'] += 1
                if first_author:
                    self.trainee_stats['first_author_research_papers'] += 1
                    self.trainee_stats['first_author_journals'].append(
                        this_summary.journal_title()
                    )

            # add the details of this paper to the paper_content list
            self.paper_content.append(this_summary.data)

        return self.trainee_stats


class Paper:
    def __init__(self, pmid, data):
        """Initialize a Paper object with a PMID, and paper object data"""
        self.pmid = pmid
        self.data = data

    def is_first_author(self, author):
        raise NotImplementedError


class PaperSummary(Paper):

    def is_review(self):
        """Is this a Review article?"""
        return True if 'Review' in self.data['pubtype'] else False

    def is_first_author(self, author):
        """Is this a first-author paper?
        First checks the first name in the authors list of the paper summary.
        Then, tries to look for co-first authors in the abstract's author_list.
        """
        standard_author = flatten_name(author)
        first_listed_author = self.data['authors'][0]['name'][:len(author)]
        return flatten_name(first_listed_author) == standard_author

    def journal_title(self):
        return self.data['source']


class PaperAbstract(Paper):
    """The PaperAbstract class accepts a pmid and author list extracted from
    the EFETCH endpoint's XLM Element Tree. It looks something like this:

    [{'ValidYN': 'Y',
      'LastName': 'Huxlin',
      'ForeName': 'Krystel R',
      'Initials': 'KR',
      'AffiliationInfo': ''},
    {'ValidYN': 'Y',
      'LastName': 'Cavanaugh',
      'ForeName': 'Matthew R',
      'Initials': 'MR',
      'AffiliationInfo': ''}]
   """

    def extract_authorship(self):
        """Given the Medline abstract XML for a paper,
        extract the author list
        """
        paper_authors = []

        for author in self.data.findall('.//Author'):
            attribs = author.attrib
            author_dict = {
                a.tag: a.text.strip() for a in author if a.text}
            if 'LastName' in author_dict.keys():
                paper_authors.append({**attribs, **author_dict})
        return paper_authors

    def is_first_author(self, author):
        """Is this a first-author paper?
        First checks the first name in the authors list of the paper summary.
        Then, tries to look for co-first authors in the abstract's author_list.
        """
        standard_author = flatten_name(author)
        author_list = self.extract_authorship()
        matching_authors = [author for author in author_list if
                            flatten_name(author['LastName'])
                            in standard_author]

        if len(matching_authors) != 1:
            print(f"Found `{len(matching_authors)}` matching author names ")
            return False

        is_listed_first = author_list[0] == matching_authors[0]
        is_co_first = 'EqualContrib' in matching_authors[0].keys()
        return (is_listed_first or is_co_first)


@click.command()
@click.argument('filepath')
def main(filepath):
    """Given a filepath to an input CSV, the script will iterate over rows of
    trainee and PI names, conduct a PubMed search, and write the results to
    a few files in the same directory.
    """

    # Check to ensure the config.py file was set up correctly
    if NCBI_EMAIL == 'abc@123.com' \
       or not re.match(r"[^@]+@[^@]+\.[^@]+", NCBI_EMAIL):
        raise ValueError('Please supply a valid email in the config.py file')

    if NCBI_API_KEY == 'abc123':
        raise ValueError('You must add your NCBI API key to config.py')

    infile = pd.read_csv(filepath)
    # Limit the number of rows for testing.
    # TODO: Make this a command line argument
    # infile = infile[:5]

    trainee_stats = []
    paper_content = []

    # Iterate over names, search pubmed, and store the results
    for row in infile.itertuples():
        a = AuthorSearch(row)
        trainee_stats.append(a.assess_trainee())
        paper_content.extend(a.paper_content)

    timestamp = dt.datetime.strftime(dt.datetime.now(), '%Y-%m-%d_%H:%M')

    # Export the trainee stats
    stats = pd.DataFrame(trainee_stats)
    outfile = pd.concat([infile, stats], axis=1)
    outfile.to_csv(f'{timestamp}_trainee_stats.csv', index=False)

    # Export the full paper content
    pc = pd.DataFrame(paper_content)
    pc['pmc'] = pc['articleids'].apply(
        lambda x: extract_identifier(x, 'pmc'))
    pc['pubmed'] = pc['articleids'].apply(
        lambda x: extract_identifier(x, 'pubmed'))
    pc.to_csv(f'{timestamp}_paper_content.csv', index=False, encoding='utf-8')


if __name__ == "__main__":
    import doctest
    doctest.testmod()
    print("Tests Finished!")
    main()
