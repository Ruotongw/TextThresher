import argparse
from collections import namedtuple
import re
import pytz, datetime


TITLE_ID = 'title:'
INSTRUCTIONS_ID = 'instructions:'
GLOSSARY_ID = 'glossary:'
DEPENDENCY_ID = 'if'
DEPENDENCY_TARGET = 'then'

QUESTION_TYPES = {'mc' : 'RADIO',
                  'dd' : 'RADIO', # old label
                  'cl' : 'CHECKBOX',
                  'tx' : 'TEXT',
                  'tb' : 'TEXT', # old label
                  'dt' : 'DATE',
                  'tm' : 'TIME'}

Dependency = namedtuple('Dependency',
    ['topic', 'question', 'answer', 'next_question'])

class ParseSchemaException(Exception):

    def __init__(self, message, errtype, file_name, linenum, timestamp, *args):
        self.message = message
        self.errtype = errtype
        self.file_name = file_name
        self.linenum = linenum
        self.timestamp = timestamp
        super(ParseSchemaException, self).__init__(message, errtype, file_name,
                                                   linenum, timestamp, *args)

def load_defaults(output):
    output['parent'] = ''
    output['topics'] = []
    output['glossary'] = {}
    output['dependencies'] = []

def parse_schema(schema_file):
    parsed_schema = {}
    load_defaults(parsed_schema)
    try:
        with open(schema_file, 'r') as f:
            linecount = 1
            for line in f:
                raw_line = line.strip()

                # Throw out blank lines
                if not raw_line:
                    linecount += 1
                    continue

                try:
                    # Infer the line type and parse accordingly
                    type_id, data = raw_line.split(None, 1)
                    if type_id.lower() == TITLE_ID:
                        parse_title(data, parsed_schema)
                    elif type_id.lower() == INSTRUCTIONS_ID:
                        parse_instructions(data, parsed_schema)
                    elif type_id.lower() == GLOSSARY_ID:
                        parse_glossary(data, parsed_schema)
                    elif type_id.lower() == DEPENDENCY_ID:
                        parse_dependency(data, parsed_schema)
                    elif unicode(type_id[0]).isnumeric():
                        parse_question_entry(type_id, data, parsed_schema)
                    else:
                        # type_id is wrong or split lines returned wrong stuffs
                        msg = "type_id {} is invalid.".format(type_id)
                        timestamp = datetime.datetime.now(pytz.utc)
                        raise ParseSchemaException(msg, "", "", 0, timestamp)

                except Exception as e:
                    timestamp = datetime.datetime.now(pytz.utc)
                    raise ParseSchemaException(e.message, type(e).__name__,
                                               schema_file, linecount,
                                               timestamp)

                linecount += 1
    except IOError as ioerr:
        msg = "I/O error({0}): {1}".format(ioerr.errno, ioerr.strerror)
        timestamp = datetime.datetime.now(pytz.utc)
        raise ParseSchemaException(msg, type(ioerr).__name__, schema_file, -1,
                                   timestamp)

    return parsed_schema

def parse_title(title, output):
    output['title'] = title

def parse_instructions(instructions, output):
    output['instructions'] = instructions

def parse_glossary(glossary_entry, output):
    if 'glossary' not in output:
        output['glossary'] = {}
    term, definition = glossary_entry.split(':', 1)
    output['glossary'][term.strip()] = definition.strip()

def parse_dependency(dependency, output):

    splitted_dependency = dependency.split(', ')
    source_phrase = splitted_dependency[0]
    target_phrase = splitted_dependency[1].split(' ')[1]
    source_topic_id, source_question_id, source_answer_id = (
        source_phrase.split('.'))
    target_question = target_phrase.split('.')[1]

    source_topic_id = int(source_topic_id)
    source_question_id = int(source_question_id)
    target_question = int(target_question)

    # Do not convert source_answer_id to int, because value might be 'any'
    # source_answer_id = int(source_answer_id)
    output['dependencies'].append(Dependency(source_topic_id,
                                             source_question_id,
                                             source_answer_id,
                                             target_question))

def infer_hint_type(question):
    match = re.search("WHERE|WHO|HOW MANY|WHEN", question, re.IGNORECASE)
    if match:
        return match.group(0).upper()
    else:
        return 'NONE';

def parse_question_entry(entry_id, data, output):
    type_bits = entry_id.split('.')
    num_bits = len(type_bits)
    if num_bits == 1:
        try:
            topics_id = int(type_bits[0])
        except ValueError:
            return
        topic_id = type_bits[0]
        if 'topics' not in output:
            output['topics'] = []
        output['topics'].append({
            'id': topic_id,
            'name': data.strip(),
            'questions': [],
        })
    elif num_bits == 2:
        topic_id, question_id = type_bits
        question_id = type_bits[1]
        topic = [t for t in output['topics'] if t['id'] == topic_id][0]
        question_type, question_text = data.split(None, 1)
        hint_type = infer_hint_type(question_text)
        if question_type in QUESTION_TYPES:
            question_type = QUESTION_TYPES[question_type]
        topic['questions'].append({
            'question_number': question_id,
            'question_text': question_text,
            'question_type': question_type,
            'answers': [],
            'hint_type': hint_type,

        })
    else:
        topic_id, question_id, answer_id = type_bits
        topic = [t for t in output['topics'] if t['id'] == topic_id][0]
        question = [q for q in topic['questions'] if q['question_number'] == question_id][0]
        question['answers'].append({
            'answer_number': answer_id,
            'answer_content': data,
        })

def print_data(output):
    print "Here's the current parsed data:"
    import pprint; pprint.pprint(output)

def print_dependencies(output):
    print "Print dependencies:"
    import pprint; pprint.pprint(output['dependencies'])


if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument('filename', nargs=1)
    args = arg_parser.parse_args()

    try:
        output = parse_schema(args.filename[0])
        print_data(output)
        # print_dependencies(output)
    except ParseSchemaException as e:
        import logging
        logging.basicConfig()
        logger = logging.getLogger(__name__)
        logger.error("In file {} line {}, {} error: {}, at UTC time {:%Y-%m-%d %H:%M:%S}"
                     .format(e.file_name, e.linenum, e.errtype, e.message,
                             e.timestamp))
