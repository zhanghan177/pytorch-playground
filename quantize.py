import hashlib
from pathlib import Path
import pprint

from collections import OrderedDict
import argparse
from utee import misc, quant, selector
import torch
import torch.backends.cudnn as cudnn
cudnn.benchmark = True


def create_filename_hash_suffix(content: str):
    h = hashlib.sha256(content.encode()).hexdigest()
    return h[:12]


def main():
    parser = argparse.ArgumentParser(description='PyTorch SVHN Example')
    parser.add_argument('--type', default='cifar10',
                        help='|'.join(selector.known_models))
    parser.add_argument('--quant_method', default='linear',
                        help='linear|minmax|log|tanh')
    parser.add_argument('--batch_size', type=int, default=100,
                        help='input batch size for training (default: 64)')
    parser.add_argument('--gpu', default=None, help='index of gpus to use')
    parser.add_argument('--ngpu', type=int, default=8,
                        help='number of gpus to use')
    parser.add_argument('--seed', type=int, default=117,
                        help='random seed (default: 1)')
    parser.add_argument(
        '--model_root', default='~/.torch/models/', help='folder to save the model')
    parser.add_argument(
        '--data_root', default='~/data/public_dataset/pytorch/', help='folder to save the model')
    parser.add_argument('--logdir', default='log/default',
                        help='folder to save to the log')

    parser.add_argument('--input_size', type=int,
                        default=224, help='input size of image')
    parser.add_argument('--n_sample', type=int, default=20,
                        help='number of samples to infer the scaling factor')
    parser.add_argument('--param_bits', type=int, default=8,
                        help='bit-width for parameters')
    parser.add_argument('--bn_bits', type=int, default=32,
                        help='bit-width for running mean and std')
    parser.add_argument('--fwd_bits', type=int, default=8,
                        help='bit-width for layer output')
    parser.add_argument('--overflow_rate', type=float,
                        default=0.0, help='overflow rate')
    parser.add_argument('--save_quantized_model', action='store_true',
                        help='Save the modified, quantized version of the model')
    args = parser.parse_args()

    args.gpu = misc.auto_select_gpu(
        utility_bound=0, num_gpu=args.ngpu, selected_gpus=args.gpu)
    args.ngpu = len(args.gpu)
    misc.ensure_dir(args.logdir)
    args.model_root = misc.expand_user(args.model_root)
    args.data_root = misc.expand_user(args.data_root)
    args.input_size = 299 if 'inception' in args.type else args.input_size
    assert args.quant_method in ['linear', 'minmax', 'log', 'tanh']
    print("=================FLAGS==================")
    for k, v in args.__dict__.items():
        print('{}: {}'.format(k, v))
    print("========================================")

    assert torch.cuda.is_available(), 'no cuda'
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed(args.seed)

    # load model and dataset fetcher
    model_raw, ds_fetcher, is_imagenet = selector.select(
        args.type, model_root=args.model_root)
    args.ngpu = args.ngpu if is_imagenet else 1

    # quantize parameters
    if args.param_bits < 32:
        state_dict = model_raw.state_dict()
        state_dict_quant = OrderedDict()
        sf_dict = OrderedDict()
        for k, v in state_dict.items():
            if 'running' in k:
                if args.bn_bits >= 32:
                    print("Ignoring {}".format(k))
                    state_dict_quant[k] = v
                    continue
                else:
                    bits = args.bn_bits
            else:
                bits = args.param_bits

            if args.quant_method == 'linear':
                sf = bits - 1. - \
                    quant.compute_integral_part(
                        v, overflow_rate=args.overflow_rate)
                v_quant = quant.linear_quantize(v, sf, bits=bits)
            elif args.quant_method == 'log':
                v_quant = quant.log_minmax_quantize(v, bits=bits)
            elif args.quant_method == 'minmax':
                v_quant = quant.min_max_quantize(v, bits=bits)
            else:
                v_quant = quant.tanh_quantize(v, bits=bits)
            state_dict_quant[k] = v_quant
            print(k, bits)
            print("comparison:")
            print(torch.max(v))
            print(torch.max(v_quant))
            print()
        model_raw.load_state_dict(state_dict_quant)

    # quantize forward activation
    if args.fwd_bits < 32:
        model_raw = quant.duplicate_model_with_quant(model_raw, bits=args.fwd_bits, overflow_rate=args.overflow_rate,
                                                     counter=args.n_sample, type=args.quant_method)
        print(model_raw)
        val_ds_tmp = ds_fetcher(
            10, data_root=args.data_root, train=False, input_size=args.input_size)
        misc.eval_model(model_raw, val_ds_tmp, ngpu=1,
                        n_sample=args.n_sample, is_imagenet=is_imagenet)

    # eval model
    val_ds = ds_fetcher(args.batch_size, data_root=args.data_root,
                        train=False, input_size=args.input_size)
    acc1, acc5 = misc.eval_model(
        model_raw, val_ds, ngpu=args.ngpu, is_imagenet=is_imagenet)

    # print sf
    print(model_raw)
    res_dict = {
        'type': args.type,
        'quant_method': args.quant_method,
        'param_bits': args.param_bits,
        'bn_bits': args.bn_bits,
        'fwd_bits': args.fwd_bits,
        'overflow_rate': args.overflow_rate,
        'acc1': f'{acc1:.4f}',
        'acc5': f'{acc5:.4f}'
    }

    res_str = str(res_dict)
    print(res_str)

    # Save modified, quantized model
    quantized_model_name = f'{args.type}-quantized-{create_filename_hash_suffix(res_str)}.pth'
    quantized_model_path = Path(args.model_root, quantized_model_name)
    if args.save_quantized_model:
        torch.save(
            {
                'model_state_dict': model_raw.state_dict(),
                'res_str': res_str,
                'res_dict': res_dict
            },
            quantized_model_path)

    with open('acc1_acc5.txt', 'a') as f:
        f.write(
            f'{res_str}, {str(quantized_model_path) if args.save_quantized_model else "not saved"}\n')


if __name__ == '__main__':
    main()
