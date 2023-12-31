"""
Description: Filter a list of reactions to only keep reactions that lead to a Blast match of the
    proteins associated with the reaction in MetaCyc database against the proteome (or the genome in option).

    Pipeline steps :
    1 - Create a dictionary associating to each reaction the set of Uniprot IDs associated to it in the MetaCyc padmet
        xrefs.
    2 - For each Uniprot ID :
        2.1 - Extract its associated protein sequence from the protein-seq-ids-reduced-70.fasta file.
        2.2 - Perform a Blastp of this sequence against the species proteome and if there is a match write the result to
            the blast_results.tsv file.
        2.3 - If there was no match and the Tblastn option is True: execute a tblastn of this sequence against the
            genome of the species and if there is a match write the result in the blast_results.tsv file.
        2.4 - If there was a match during the Blastp or Tblastn: add the reaction associated with the Uniprot ID to the
            set of reactions to be kept.
    4. Returns the reaction set that led to a blast match.
"""
import os
import logging
import padmet.classes.padmetSpec

from padmet.classes.padmetSpec import PadmetSpec
from Bio.Blast.Applications import NcbiblastpCommandline, NcbitblastnCommandline
from Bio import SeqIO
from typing import Set, List, Tuple, Dict


# CONSTANTS ============================================================================================================

# Blast
BLAST_HEADER = ["sseqid", "evalue", "bitscore", "pident", "length"]


# ENVIRONMENT ==========================================================================================================
def get_directories(output: str) -> Tuple[str, str, str, str, str]:
    """ Returns the path to the main directories and files to manage

    Parameters
    ----------
    output: str
        Path of the parent output directory

    Returns
    -------
    str
        Directory to store fasta sequence of proteins
    str
        Directory to store results files
    str
        Path to the blast result file to write blast match results
    str
        Path to file indicating uniprot IDs linked to all reactions
    str
        Path to the log file
    """
    seq_dir = os.path.join(output, 'sequences')
    res_dir = os.path.join(output, 'results')

    blast_res_file = os.path.join(res_dir, 'blast_results.tsv')
    rxn_prot_file = os.path.join(res_dir, 'rxn_prot.tsv')

    log_file = os.path.join(output, 'blastp_validation.log')

    return seq_dir, res_dir, blast_res_file, rxn_prot_file, log_file


# FUNCTIONS ============================================================================================================

def get_uniprot_ids_from_rxn(rxn: str, padmet_spec: padmet.classes.padmetSpec.PadmetSpec) -> Set[str]:
    """ Obtain the set of Uniprot IDs linked to a reaction.

    Parameters
    ----------
    rxn : str
        Reaction to find Uniprot IDs associated with
    padmet_spec : padmet.classes.padmetSpec.PadmetSpec
        PadmetSpec of DataBase

    Returns
    -------
    Set[str]
        Set of Uniprot IDs liked to the reaction
    """
    logging.info(f'Extract Uniprot IDs linked to reaction {rxn}')
    prot_keys = ('UNIPROT', 'PID')
    suffix = '_70'
    uniprot_set = set()

    try:
        xrefs = padmet_spec.dicOfNode[rxn + '_xrefs'].misc
    except KeyError:
        logging.info(f'0 Protein IDs linked to reaction {rxn}\n')
        return set()

    for prot_key in prot_keys:
        try:
            uniprot_ids = xrefs[prot_key + suffix]
            uniprot_set = uniprot_set.union(set([f'{prot_key}:{prot_id}' for prot_id in uniprot_ids]))
        except KeyError:
            pass

    logging.info(f'{len(uniprot_set)} Protein IDs linked to reaction {rxn}\n')
    return uniprot_set


def create_dirs_and_init_result_file(seq_dir, res_dir, blast_res_file):
    """ Create the 'sequences' and 'results' directories if they don't exist and create the results file."""
    # Directories creation
    if not os.path.exists(seq_dir):
        os.mkdir(seq_dir)
    if not os.path.exists(res_dir):
        os.mkdir(res_dir)

    # Results file creation and initialization
    f = open(blast_res_file, 'w')
    f.write('\t'.join(['Reaction', 'Uniprot ID', 'Sequence', 'E value', 'Bit score', 'Identity (%)', 'Length',
                       'Blast method\n']))
    f.close()
    logging.info(f'Blast results tsv file created in : {blast_res_file}')


def create_rxn_prot_tsv(rxn_prot_dict: Dict[str, Set[str]], rxn_prot_file: str):
    """ Fill the file rxn_prot.tsv associating reaction to their associated uniprot IDs

    Parameters
    ----------
    rxn_prot_dict: dict[str, set[str]]
        Dictionary associating reaction to their associated uniprot IDs
    rxn_prot_file: str
        Path to the rxn_prot.tsv file
    """
    with open(rxn_prot_file, 'w') as f:
        f.write('Rxn ID\tNb prot IDs\tProt IDs (sep=;)\n')
        for rxn, pid_set in rxn_prot_dict.items():
            f.write(f'{rxn}\t{len(pid_set)}\t{";".join(pid_set)}\n')


def get_uniprot_seq(uni_id: str, prot_fasta: dict) -> str:
    """Obtain the sequence associated with an Uniprot IDs from the line of the protein-seq-ids-reduced-70.fasta file.

    Parameters
    ----------
    uni_id : str
        Uniprot ID to find the protein sequence associated with.
    prot_fasta: dict
        Dictionary of records of proteins sequences

    Returns
    -------
    str
        The sequence associated with the Uniprot ID.
    """
    logging.info(f'\nGet sequence for {uni_id} protein')
    try:
        seq = prot_fasta[uni_id].seq
        return seq
    except KeyError:
        logging.warning(f'\nNo sequence corresponding to {uni_id} in {prot_fasta} file.')
        return ''


def run_blastp(prot_seq: str, uniprot_id: str, rxn: str, species_proteome: str, seq_dir: str, blast_res_file: str,
               e_value: float) -> bool:
    """Runs a Blastp of the protein sequence of the Uniprot ID associated with a reaction, against rhe species proteome.
    If there is a match, writes the result in the results file and returns True, otherwise just returns False.

    Parameters
    ----------
    prot_seq: str
        Sequence to use as query for Blastp
    uniprot_id: str
        Uniprot ID corresponding to the protein sequence
    rxn: str
        Reaction within the Uniprot ID has been associated to.
    species_proteome: str
        Proteome of the species
    seq_dir: str
        Directory where sequences are stored
    blast_res_file: str
        File to write results information of the blast
    e_value


    Returns
    -------
    bool
        True if the Blastp of the protein sequence against the species proteome found a match.
    """
    uniprot_id = uniprot_id.split(':')[1]

    logging.info(f'Blastp of protein {uniprot_id} for reaction {rxn} against {species_proteome} proteome')

    out_fmt_arg = '"%s %s"' % (6, " ".join(BLAST_HEADER))
    query_fasta = os.path.join(seq_dir, f'{uniprot_id}.fasta')
    if not os.path.exists(query_fasta):
        with open(query_fasta, 'w') as fquery:
            fquery.write(f'>{uniprot_id}\n{prot_seq}\n')

    blastp_output = NcbiblastpCommandline(query=query_fasta, subject=species_proteome, evalue=e_value,
                                          outfmt=out_fmt_arg)()[0]
    if blastp_output != '':
        with open(blast_res_file, 'a') as res_file:
            output = blastp_output.split('\n')
            for match in output[:-1]:
                res_file.write('\t'.join([rxn, uniprot_id, match, 'Blastp\n']))
        return True
    return False


def run_tblastn(prot_seq: str, uniprot_id: str, rxn: str, species_genome: str, seq_dir: str, blast_res_file: str,
                e_value: float) -> bool:
    """Runs a Tblastn of the protein sequence of the Uniprot ID associated with a reaction, against rhe species genome.
    If there is a match, writes the result in the results file and returns True, otherwise just returns False.

    Parameters
    ----------
    prot_seq : str
        Sequence to use as query for Tblastn
    uniprot_id : str
        Uniprot ID corresponding to the protein sequence
    rxn : str
        Reaction within the Uniprot ID has been associated to.
    species_genome: str
        Genome of the species
    seq_dir: str
        Directory where sequences are stored
    blast_res_file: str
        File to write results information of the blast
    e_value: float

    Returns
    -------
    bool
        True if the Tblastn of the protein sequence against the species genome found a match.
    """
    uniprot_id = uniprot_id.split(':')[1]
    logging.info(f'TBlastN of protein {uniprot_id} for reaction {rxn} against {species_genome} genome.')
    out_fmt_arg = '"%s %s"' % (6, " ".join(BLAST_HEADER))
    query_fasta = os.path.join(seq_dir, f'{uniprot_id}.fasta')
    if not os.path.exists(query_fasta):
        with open(query_fasta, 'w') as fquery:
            fquery.write(f'>{uniprot_id}\n{prot_seq}\n')

    tblastn_output = NcbitblastnCommandline(query=query_fasta, subject=species_genome, evalue=e_value,
                                            outfmt=out_fmt_arg)()[0]
    if tblastn_output != '':
        with open(blast_res_file, 'a') as res_file:
            output = tblastn_output.split('\n')
            for match in output[:-1]:
                res_file.write('\t'.join([rxn, uniprot_id, match, 'TBlastN\n']))
        return True
    return False


# MAIN FUNCTION ========================================================================================================

def validation_blastp(rxn_list: List[str], output: str, db_padmet: str, prot_fasta: str, species_proteome: str,
                      species_genome: str = None, e_value: float = 1e-10) -> Set[str]:
    """
    1 - Create a dictionary associating to each reaction the set of Uniprot IDs associated to it in the MetaCyc padmet
        xrefs.
    2 - For each Uniprot ID :
        2.1 - Extract its associated protein sequence from the protein-seq-ids-reduced-70.fasta file.
        2.2 - Perform a Blastp of this sequence against the species proteome and if there is a match write the result to
            the blast_results.tsv file.
        2.3 - If there was no match and the Tblastn option is True: execute a tblastn of this sequence against the
            genome of the species and if there is a match write the result in the blast_results.tsv file.
        2.4 - If there was a match during the Blastp or Tblastn: add the reaction associated with the Uniprot ID to the
            set of reactions to be kept.
    4. Returns the reaction set that led to a blast match.

    Parameters
    ----------
    rxn_list: list[str]
        List of reaction to test : must be a MetaCyc id
    output: str
    db_padmet: str
    prot_fasta: str
    species_proteome: str
    species_genome: str (default=None)
    e_value: float

    Returns
    -------
    set[str]
        Set of the reactions that led to a blastp or tblastn match
    """
    padmet_spec = PadmetSpec(db_padmet)
    prot_fasta = SeqIO.to_dict(SeqIO.parse(prot_fasta, 'fasta'))
    tblastn = species_genome is not None
    seq_dir, res_dir, blast_res_file, rxn_prot_file, log_file = get_directories(output)
    logging.basicConfig(filename=log_file, level=logging.INFO, format='%(message)s', force=True)

    logging.info('Start searching alignments\n'
                 '==========================\n')

    # 2 - Get uniprot_ids linked to all these reactions
    logging.info('Start getting proteins linked to each reaction\n'
                 '==============================================\n')

    uniprot_dict = dict()
    for rxn in rxn_list:
        uniprot_dict[rxn] = get_uniprot_ids_from_rxn(rxn, padmet_spec)
    alignment_total = sum(len(uni_id) for uni_id in uniprot_dict.values())
    create_dirs_and_init_result_file(seq_dir, res_dir, blast_res_file)
    create_rxn_prot_tsv(uniprot_dict, rxn_prot_file)

    # Loop on rxn and uniprot IDs
    logging.info('Start blast alignments\n'
                 '======================\n')

    kept_rxn_set = set()
    alignment_number = 0
    for rxn, uniprot_ids in uniprot_dict.items():
        for uni_id in uniprot_ids:
            # Get sequence
            seq = get_uniprot_seq(uni_id, prot_fasta)

            # Blastp sp proteome
            keep_rxn = run_blastp(seq, uni_id, rxn, species_proteome, seq_dir, blast_res_file, e_value)
            # Keep reactions with alignment result
            if keep_rxn:
                kept_rxn_set.add(rxn)

            # Tblastn if not kept
            elif tblastn:
                keep_rxn = run_tblastn(seq, uni_id, rxn, species_genome, seq_dir, blast_res_file, e_value)
                # Keep reactions with alignment result
                if keep_rxn:
                    kept_rxn_set.add(rxn)

            # Inform advancement
            alignment_number += 1
            logging.info(f'{alignment_number}/{alignment_total} done')

    logging.info(f'\n\n{len(kept_rxn_set)} reactions with alignment : {", ".join(kept_rxn_set)}')
    logging.info(f'=================\n'
                 f'End of alignments')
    return kept_rxn_set
