
        #################################################
        ### THIS FILE WAS AUTOGENERATED! DO NOT EDIT! ###
        #################################################

from nb_007 import *
import pandas as pd, re, spacy, html, os
from spacy.symbols import ORTH
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

BOS,FLD,UNK,PAD = 'xxbos','xxfld','xxunk','xxpad'
TOK_UP,TK_REP,TK_WREP = 'xxup','xxrep','xxwrep'

def partition(a, sz):
    """splits iterables a in equal parts of size sz"""
    return [a[i:i+sz] for i in range(0, len(a), sz)]

def partition_by_cores(a, n_cpus):
    return partition(a, len(a)//n_cpus + 1)

def num_cpus():
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count()

class SpacyTokenizer():
    "Little wrapper around a spacy tokenizer"

    def __init__(self, lang):
        self.tok = spacy.load(lang)

    def tokenizer(self, t):
        return [t.text for t in self.tok.tokenizer(t)]

    def add_special_cases(self, toks):
        for w in toks:
            self.tok.tokenizer.add_special_case(w, [{ORTH: w}])

class Tokenizer():
    def __init__(self, tok_fn=SpacyTokenizer, lang:str='en', rules:Collection[Callable[[str],str]]=None,
                 special_cases:Collection[str]=None, n_cpus = None):
        self.tok_fn,self.lang,self.special_cases = tok_fn,lang,special_cases
        self.rules = rules if rules else []
        for rule in self.rules:
            if hasattr(rule, 'compile'): rule.compile()
        self.n_cpus = n_cpus or num_cpus()//2

    def __repr__(self):
        res = f'Tokenizer {self.tok_fn.__name__} in {self.lang} with the following rules:\n'
        for rule in self.rules: res += f' - {rule.__name__}\n'
        return res

    def proc_text(self, t, tok):
        for rule in self.rules: t = rule(t)
        return tok.tokenizer(t)

    def process_all_1thread(self, texts):
        tok = self.tok_fn(self.lang)
        if self.special_cases: tok.add_special_cases(self.special_cases)
        return [self.proc_text(t, tok) for t in texts]

    def process_all(self, texts):
        if self.n_cpus <= 1: return self.process_all_1thread(texts)
        with ProcessPoolExecutor(self.n_cpus) as e:
            return sum(e.map(self.process_all_1thread, partition_by_cores(texts, self.n_cpus)), [])

def sub_br(t):
    "Replaces the <br /> by \n"
    re_br = re.compile(r'<\s*br\s*/?>', re.IGNORECASE)
    return re_br.sub("\n", t)

def spec_add_spaces(t):
    return re.sub(r'([/#])', r' \1 ', t)

def rm_useless_spaces(t):
    return re.sub(' {2,}', ' ', t)

def replace_rep(t):
    def _replace_rep(m):
        c,cc = m.groups()
        return f' {TK_REP} {len(cc)+1} {c} '
    re_rep = re.compile(r'(\S)(\1{3,})')
    return re_rep.sub(_replace_rep, t)

def replace_wrep(t):
    def _replace_wrep(m):
        c,cc = m.groups()
        return f' {TK_WREP} {len(cc.split())+1} {c} '
    re_wrep = re.compile(r'(\b\w+\W+)(\1{3,})')
    return re_wrep.sub(_replace_wrep, t)

def deal_caps(t):
    res = []
    for s in re.findall(r'\w+|\W+', t):
        res += ([TOK_UP,s.lower()] if (s.isupper() and (len(s)>2)) else [s.lower()])
    return ' '.join(res)

def fixup(x):
    re1 = re.compile(r'  +')
    x = x.replace('#39;', "'").replace('amp;', '&').replace('#146;', "'").replace(
        'nbsp;', ' ').replace('#36;', '$').replace('\\n', "\n").replace('quot;', "'").replace(
        '<br />', "\n").replace('\\"', '"').replace('<unk>',UNK).replace(' @.@ ','.').replace(
        ' @-@ ','-').replace('\\', ' \\ ')
    return re1.sub(' ', html.unescape(x))

rules = [sub_br, spec_add_spaces, rm_useless_spaces, replace_rep, replace_wrep, deal_caps, fixup]

def get_chunk_length(csv_name, chunksize):
    dfs = pd.read_csv(csv_name, header=None, chunksize=chunksize)
    l = 0
    for _ in dfs: l+=1
    return l

def get_total_length(csv_name, chunksize):
    dfs = pd.read_csv(csv_name, header=None, chunksize=chunksize)
    l = 0
    for df in dfs: l+=len(df)
    return l

def maybe_copy(old_fnames, new_fnames):
    for old_fname,new_fname in zip(old_fnames, new_fnames):
        if not os.path.isfile(new_fname) or os.path.getmtime(new_fname) < os.path.getmtime(old_fname):
            shutil.copyfile(old_fname, new_fname)

class Vocab():
    "Contains the correspondance between numbers and tokens and numericalizes"

    def __init__(self, path):
        self.itos = pickle.load(open(path/'itos.pkl', 'rb'))
        self.stoi = collections.defaultdict(int,{v:k for k,v in enumerate(self.itos)})

    def numericalize(self, t):
        return [self.stoi[w] for w in t]

    def textify(self, nums):
        return ' '.join([self.itos[i] for i in nums])

    @classmethod
    def create(cls, path, tokens, max_vocab, min_freq):
        freq = Counter(p for o in tokens for p in o)
        itos = [o for o,c in freq.most_common(max_vocab) if c > min_freq]
        itos.insert(0, PAD)
        if UNK in itos: itos.remove(UNK)
        itos.insert(0, UNK)
        pickle.dump(itos, open(path/'itos.pkl', 'wb'))
        with open(path/'numericalize.log','w') as f: f.write(str(len(itos)))
        return cls(path)

TextMtd = IntEnum('TextMtd', 'CSV TOK IDS')

import shutil

class TextDataset():
    "Put a train.csv and valid.csv files in a folder and this will take care of the rest."

    def __init__(self, path, tokenizer, vocab=None, max_vocab=30000, chunksize=10000, name='train',
                 min_freq=2, n_labels=1, create_mtd=TextMtd.CSV):
        self.path,self.tokenizer,self.max_vocab,self.min_freq = Path(path/'tmp'),tokenizer,max_vocab,min_freq
        self.chunksize,self.name,self.n_labels,self.create_mtd = chunksize,name,n_labels,create_mtd
        self.vocab=vocab
        os.makedirs(self.path, exist_ok=True)
        if not self.check_toks(): self.tokenize()
        if not self.check_ids():  self.numericalize()

        if self.vocab is None: self.vocab = Vocab(self.path)
        self.ids = np.load(self.path/f'{self.name}_ids.npy')
        self.labels = np.load(self.path/f'{self.name}_lbl.npy')

    def general_check(self, pre_files, post_files):
        "Checks that post_files exist and were modified after all the prefiles."
        if not np.all([os.path.isfile(fname) for fname in post_files]): return False
        for pre_file in pre_files:
            if os.path.getmtime(pre_file) > os.path.getmtime(post_files[0]): return False
        return True

    def check_ids(self):
        if self.create_mtd >= TextMtd.IDS: return True
        if not self.general_check([self.tok_files[0],self.id_files[1]], self.id_files): return False
        itos = pickle.load(open(self.id_files[1], 'rb'))
        with open(self.id_files[2]) as f:
            if len(itos) != int(f.read()) or len(itos) > self.max_vocab + 2: return False
        toks,ids = np.load(self.tok_files[0]),np.load(self.id_files[0])
        if len(toks) != len(ids): return False
        return True

    def check_toks(self):
        if self.create_mtd >= TextMtd.TOK: return True
        if not self.general_check([self.csv_file], self.tok_files): return False
        with open(self.tok_files[1]) as f:
            if repr(self.tokenizer) != f.read(): return False
        return True

    def tokenize(self):
        print(f'Tokenizing {self.name}. This might take a while so you should grab a coffee.')
        curr_len = get_chunk_length(self.csv_file, self.chunksize)
        dfs = pd.read_csv(self.csv_file, header=None, chunksize=self.chunksize)
        tokens,labels = [],[]
        for _ in progress_bar(range(curr_len), leave=False):
            df = next(dfs)
            lbls = df.iloc[:,range(self.n_labels)].values.astype(np.int64)
            texts = f'\n{BOS} {FLD} 1 ' + df[self.n_labels].astype(str)
            for i in range(self.n_labels+1, len(df.columns)):
                texts += f' {FLD} {i-n_lbls} ' + df[i].astype(str)
            toks = self.tokenizer.process_all(texts)
            tokens += toks
            labels += list(np.squeeze(lbls))
        np.save(self.tok_files[0], np.array(tokens))
        np.save(self.path/f'{self.name}_lbl.npy', np.array(labels))
        with open(self.tok_files[1],'w') as f: f.write(repr(self.tokenizer))

    def numericalize(self):
        print(f'Numericalizing {self.name}.')
        toks = np.load(self.tok_files[0])
        if self.vocab is None: self.vocab = Vocab.create(self.path, toks, self.max_vocab, self.min_freq)
        ids = np.array([self.vocab.numericalize(t) for t in toks])
        np.save(self.id_files[0], ids)

    def clear(self):
        files = [self.path/f'{self.name}_{suff}.npy' for suff in ['ids','tok','lbl']]
        files.append(self.path/f'{self.name}.csv')
        for file in files:
            if os.path.isfile(file): os.remove(file)

    @property
    def csv_file(self): return self.path/f'{self.name}.csv'
    @property
    def tok_files(self): return [self.path/f'{self.name}_tok.npy', self.path/'tokenize.log']
    @property
    def id_files(self):
        return [self.path/f'{self.name}_ids.npy', self.path/'itos.pkl', self.path/'numericalize.log']

    @classmethod
    def from_ids(cls, folder, train_ids='train_ids.npy', valid_ids='valid_ids.npy', itos = 'itos.pkl',
                 train_lbl='train_lbl.npy', valid_lbl='train_lbl.npy', **kwargs):
        orig = [Path(folder/file) for file in [train_ids, valid_ids, train_lbl, valid_lbl, itos]]
        dest = ['train_ids.npy', 'valid_ids.npy', 'train_lbl.npy', 'validl_lbl.npy', 'itos.pkl']
        dest = [Path(folder)/'tmp'/file for file in dest]
        maybe_copy(orig, dest)
        return (cls(folder, None, name='train', create_mtd=TextMtd.IDS, **kwargs),
                cls(folder, None, name='valid', create_mtd=TextMtd.IDS, **kwargs))

    @classmethod
    def from_tokens(cls, folder, train_tok='train_tok.npy', valid_tok='valid_tok.npy',
                 train_lbl='train_lbl.npy', valid_lbl='train_lbl.npy', **kwargs):
        orig = [Path(folder/file) for file in [train_tok, valid_tok, train_lbl, valid_lbl]]
        dest = ['train_tok.npy', 'valid_tok.npy', 'train_tok.npy', 'validl_tok.npy']
        dest = [Path(folder)/'tmp'/file for file in dest]
        maybe_copy(orig, dest)
        train_ds = cls(folder, None, name='train', create_mtd=TextMtd.TOK, **kwargs)
        return (train_ds, cls(folder, None, name='valid', vocab=train_ds.vocab, create_mtd=TextMtd.TOK, **kwargs))

    @classmethod
    def from_csv(cls, folder, tokenizer, train_csv='train.csv', valid_csv='valid.csv', **kwargs):
        orig = [Path(folder)/file for file in [train_csv, valid_csv]]
        dest = [Path(folder)/'tmp'/file for file in ['train.csv', 'valid.csv']]
        maybe_copy(orig, dest)
        train_ds = cls(folder, tokenizer, name='train', **kwargs)
        return (train_ds, cls(folder, tokenizer, name='valid', vocab=train_ds.vocab, **kwargs))

    @classmethod
    def from_folder(cls, folder, tokenizer, classes=None, train_name='train', valid_name='valid',
                    shuffle=True, **kwargs):
        path = Path(folder)/'tmp'
        os.makedirs(path, exist_ok=True)
        if classes is None: classes = [cls.name for cls in find_classes(Path(folder/train_name))]
        for name in [train_name, valid_name]:
            texts,labels = [],[]
            for idx,label in enumerate(classes):
                for fname in (Path(folder)/name/label).glob('*.*'):
                    texts.append(fname.open('r', encoding='utf8').read())
                    labels.append(idx)
            texts,labels = np.array(texts),np.array(labels)
            if shuffle:
                idx = np.random.permutation(len(texts))
                texts,labels = texts[idx],labels[idx]
            df = pd.DataFrame({'text':texts, 'labels':labels}, columns=['labels','text'])
            if os.path.isfile(path/f'{name}.csv'):
                if get_total_length(path/f'{name}.csv', 10000) != len(df):
                    df.to_csv(path/f'{name}.csv', index=False, header=False)
            else: df.to_csv(path/f'{name}.csv', index=False, header=False)
        train_ds = cls(folder, tokenizer, name='train', **kwargs)
        return (train_ds, cls(folder, tokenizer, name='valid', vocab=train_ds.vocab, **kwargs))