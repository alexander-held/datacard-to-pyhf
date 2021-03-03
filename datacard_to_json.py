import json
import logging
import sys
from typing import Any


log = logging.getLogger(__name__)


def json_str(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=4)


def restructure_observations(observations: list) -> list:
    """build list of dictionaries with observed yields"""
    obs_dict_list = []
    # not clear how multi-bin channels are specified here
    for i_ch, channel in enumerate(observations[0].split()[1:]):
        obs = float(observations[1].split()[1:][i_ch])  # could use int for data
        obs_dict_list.append({"data": [obs], "name": channel})
    log.debug(f"\nobs dict:\n{json_str(obs_dict_list)}\n")
    return obs_dict_list


def restructure_channels(samples: list) -> list:
    """build list of channels with samples and their observed yields"""
    ch_dict_list = []
    # this assumes order bin - process - process - rate
    # loop over channels
    channel_names = samples[0].split()[1:]
    sample_names = samples[1].split()[1:]
    yields = [float(y) for y in samples[3].split()[1:]]
    # loop over channels
    for ch in sorted(set(channel_names)):
        # get indices of current channel
        ch_idx = [i for i, c in enumerate(channel_names) if c == ch]
        sample_dict_list = []
        for i_sam, sample in enumerate([sample_names[i] for i in ch_idx]):
            # include a placeholder for sample modifiers
            sample_dict_list.append(
                {"name": sample, "data": [yields[ch_idx[i_sam]]], "modifiers": []}
            )
        ch_dict_list.append({"name": ch, "samples": sample_dict_list})
    log.debug(f"\nch dict:\n{json_str(ch_dict_list)}\n")
    return ch_dict_list


def restructure_modifiers(
    modifiers: list, channel_names: list, channel_yields: list, sample_names: list
) -> dict:
    """build a dictionary with modifiers per sample from datacard"""
    n_processes = len(sample_names)

    # placeholder collecting modifiers per channel and sample, list of dicts
    # example: modifier_dict[ch_name][sam_name] is list of modifiers
    modifier_dict = {}
    for ch in channel_names:
        modifier_dict.update({ch: {}})
        for s in sample_names:
            modifier_dict[ch].update({s: []})

    for line in modifiers:
        # parse each modifier
        line_split = line.split()
        syst_name = line_split[0]
        syst_type = line_split[1]
        if syst_type == "gmN":
            # additional entry needed for gammas
            # currently unclear how extrapolation factor enters
            n_evts_CR = int(line_split[2])
            # see https://cms-analysis.github.io/HiggsAnalysis-CombinedLimit/part2/settinguptheanalysis/#a-simple-counting-experiment
            stat_unc = 1 / (1 + n_evts_CR) ** 0.5
            norm_effects = line_split[3 : 3 + n_processes]
            # override extrapolation factors with rel. stat unc
            norm_effects = [stat_unc if n != "-" else n for n in norm_effects]
        else:
            norm_effects = line_split[2 : 2 + n_processes]
        norm_effects = [float(n) if n != "-" else 0.0 for n in norm_effects]
        log.debug(f"syst {syst_name} with type {syst_type} and effects {norm_effects}")

        for i, norm_effect in enumerate(norm_effects):
            # go through each sample affected by a modifier
            if norm_effect == 0.0:
                continue  # no effect, skip
            channel_name = channel_names[i]
            sample_name = sample_names[i]
            log.debug(
                f" - norm effect {norm_effect} for {sample_name} in {channel_name}"
            )
            if syst_type == "lnN":
                modifier_dict[channel_name][sample_name].append(
                    {
                        "name": syst_name,
                        "type": "normsys",
                        "data": {"hi": norm_effect, "lo": 2 - norm_effect},
                    }
                )
            elif syst_type == "gmN":
                # this needs access to channel yields to calculate absolute stat. unc.
                abs_stat_unc = norm_effect * channel_yields[i]
                modifier_dict[channel_name][sample_name].append(
                    {"name": syst_name, "type": "staterror", "data": [abs_stat_unc]}
                )
            else:
                raise NotImplementedError
    log.debug(f"\nmodifier dict:\n{json_str(modifier_dict)}\n")
    return modifier_dict


def get_sections_dict(datacard: list) -> dict:
    """extract info from datacard into dictionary"""
    sections_list = []
    current_section = []
    for line in datacard:
        line_stripped = line.strip()
        if len(line_stripped) > 0 and line_stripped[0] == "#":
            # skip comments
            continue
        if line_stripped[0] == "-":
            # end of section
            sections_list.append(current_section)
            current_section = []
        else:
            current_section.append(line_stripped)
        if line == datacard[-1]:
            # append last section
            sections_list.append(current_section)
    sections_dict = {}
    # find "general" section with imax etc.
    # seems to be first usually
    # not clear yet that this is needed
    sections_dict.update({"general": sections_list.pop(0)})
    # data yields, identified by "observation"
    idx = next(
        i
        for i, s in enumerate(sections_list)
        if any(["observation" in l[0:11] for l in s])
    )
    sections_dict.update({"observations": sections_list.pop(idx)})
    # sample yields, identified by "rate"
    idx = next(
        i for i, s in enumerate(sections_list) if any(["rate" in l[0:4] for l in s])
    )
    sections_dict.update({"channels": sections_list.pop(idx)})
    # systematics, last in list (need better identifier)
    sections_dict.update({"modifiers": sections_list.pop(-1)})

    # full list of channels and samples from datacard (including duplications)
    channel_names = sections_dict["channels"][0].split()[1:]
    channel_yields = [float(y) for y in sections_dict["channels"][3].split()[1:]]
    sample_names = sections_dict["channels"][1].split()[1:]

    # convert observations into dict
    sections_dict["observations"] = restructure_observations(
        sections_dict["observations"]
    )

    # convert channel information (sample yields) into dict
    sections_dict["channels"] = restructure_channels(sections_dict["channels"])

    # convert modifier information into dict
    # needs access to full lists of channels (+ yields) and sample names
    sections_dict["modifiers"] = restructure_modifiers(
        sections_dict["modifiers"], channel_names, channel_yields, sample_names
    )
    return sections_dict


def sections_dict_to_workspace(sections_dict: dict) -> dict:
    """convert dictionary with info from datacard into workspace"""
    ws = {}
    # need to add signal POI manually, assuming signal is first process
    for channel in sections_dict["channels"]:
        for i, sample in enumerate(channel["samples"]):
            # attache modifiers to this sample for this channel
            sample["modifiers"] = sections_dict["modifiers"][channel["name"]][
                sample["name"]
            ]
            if i == 0:
                # this should be signal, attach normfactor
                sample["modifiers"].append(
                    {"data": None, "name": "r", "type": "normfactor"}
                )
    ws.update({"channels": sections_dict["channels"]})
    ws.update(
        {"measurements": [{"config": {"parameters": [], "poi": "r"}, "name": "meas"}]}
    )
    ws.update({"observations": sections_dict["observations"]})
    ws.update({"version": "1.0.0"})
    return ws


def datacard_to_json(datacard: list) -> dict:
    sections_dict = get_sections_dict(datacard)
    ws = sections_dict_to_workspace(sections_dict)
    log.debug(f"HistFactory workspace in JSON:\n{json_str(ws)}\n")
    return ws


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

    with open(sys.argv[-1]) as f:
        datacard = f.readlines()

    ws = datacard_to_json(datacard)
    ws_name = ".".join(sys.argv[-1].split(".")[0:-1]) + ".json"

    log.info(f"saving workspace as {ws_name}")
    with open(ws_name, "w") as f:
        f.write(json_str(ws))
