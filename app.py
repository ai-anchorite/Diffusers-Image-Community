import gradio as gr
import torch
import devicetorch
import os
import webbrowser

from gradio_imageslider import ImageSlider
from huggingface_hub import hf_hub_download

from diffusers import AutoencoderKL, TCDScheduler
from diffusers.models.model_loading_utils import load_state_dict
from controlnet_union import ControlNetModel_Union
from pipeline_fill_sd_xl import StableDiffusionXLFillPipeline

from datetime import datetime
from pathlib import Path
from PIL import Image

MODELS = {
    "RealVisXL V5.0 Lightning": "SG161222/RealVisXL_V5.0_Lightning",
}
DEVICE = devicetorch.get(torch)
OUTPUT_DIR = "outputs" 

pipe = None
global_image = None
latest_result = None


def init():
    global pipe

    if pipe is None:
        
        config_file = hf_hub_download(
            "xinsir/controlnet-union-sdxl-1.0",
            filename="config_promax.json",
        )

        config = ControlNetModel_Union.load_config(config_file)
        controlnet_model = ControlNetModel_Union.from_config(config)
        model_file = hf_hub_download(
            "xinsir/controlnet-union-sdxl-1.0",
            filename="diffusion_pytorch_model_promax.safetensors",
        )
        state_dict = load_state_dict(model_file)
        model, _, _, _, _ = ControlNetModel_Union._load_pretrained_model(
            controlnet_model, state_dict, model_file, "xinsir/controlnet-union-sdxl-1.0"
        )
        model.to(device=DEVICE, dtype=torch.float16)

        vae = AutoencoderKL.from_pretrained(
            "madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16
        ).to(DEVICE)

        pipe = StableDiffusionXLFillPipeline.from_pretrained(
            "SG161222/RealVisXL_V5.0_Lightning",
            torch_dtype=torch.float16,
            vae=vae,
            controlnet=model,
            variant="fp16",
        ).to(DEVICE)

        pipe.scheduler = TCDScheduler.from_config(pipe.scheduler.config)


def fill_image(prompt, image, model_selection, guidance_scale, steps):
    global latest_result 
    init()
    source = image["background"]
    mask = image["layers"][0]

    alpha_channel = mask.split()[3]
    binary_mask = alpha_channel.point(lambda p: p > 0 and 255)
    cnet_image = source.copy()
    cnet_image.paste(0, (0, 0), binary_mask)

    (
        prompt_embeds,
        negative_prompt_embeds,
        pooled_prompt_embeds,
        negative_pooled_prompt_embeds,
    ) = pipe.encode_prompt(prompt, DEVICE, True)


    for image in pipe(
        prompt_embeds=prompt_embeds,
        negative_prompt_embeds=negative_prompt_embeds,
        pooled_prompt_embeds=pooled_prompt_embeds,
        negative_pooled_prompt_embeds=negative_pooled_prompt_embeds,
        image=cnet_image,
        guidance_scale=guidance_scale,
        num_inference_steps=steps,
    ):
        yield image, cnet_image

    image = image.convert("RGBA")
    cnet_image.paste(image, (0, 0), binary_mask)
    latest_result = cnet_image  
    
    # Save the output image and store the full path to use for gallery
    saved_filename = save_output(latest_result)    
    full_path = os.path.abspath(saved_filename)
    print(f"Image saved as: {full_path}")
    
    yield source, cnet_image


def save_output(latest_result):
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        current_time = datetime.now().strftime("%Y%m%d%H%M%S")
        new_filename = f"img_{current_time}.png"
        full_path = os.path.join("outputs", new_filename)
        latest_result.save(full_path)
        return full_path
    except Exception as e:
        print(f"Error saving image: {e}")
        return None
        
def open_outputs_folder():
    try:
        # Ensure the outputs directory exists
        Path(OUTPUT_DIR).mkdir(exist_ok=True)
        folder_uri = Path(OUTPUT_DIR).absolute().as_uri()
        webbrowser.open(folder_uri)
        return "Opened outputs folder (folder can be shy and hide behind active windows)."
    except Exception as e:
        return f"Error opening outputs folder: {str(e)}"
    
def clear_result():
    return gr.update(value=None)

def set_img(image):
    global global_image
    global_image = image["background"]

def resize(image, size):
    global global_image
    size = (int(size) // 8) * 8
    source = global_image.copy()
    source.thumbnail((size, size), Image.LANCZOS)

    resized_w, resized_h = source.size
    resized_w = (resized_w // 8) * 8
    resized_h = (resized_h // 8) * 8
    source = source.crop((0, 0, resized_w, resized_h))

    w, h = global_image.size
    max = (w // 8) * 8
    return gr.update(value=source, canvas_size=(w,h)), gr.update(maximum=max, visible=True), gr.update(visible=True), gr.update(visible=True), gr.update(visible=True)

# send result back to input
def send_to_input(result_slider):
    if latest_result is not None and result_slider is not None:
        global global_image
        global_image = latest_result
        return gr.update(value=latest_result), gr.update(value=None)
    # If no result or result slider is empty, don't update anything
    return gr.update(), gr.update()
    

with gr.Blocks(fill_width=True) as demo:
    with gr.Row():
        input_image = gr.ImageMask(
            type="pil",
            label="Input Image",
            layers=False,
            sources=["upload"],
        )

        result = ImageSlider(
            show_download_button=False,
            interactive=False,
            label="Generated Image",
        )
        
    with gr.Row():
        run_button = gr.Button("Generate")
        
    with gr.Row():
        model_selection = gr.Dropdown(
            choices=list(MODELS.keys()),
            value="RealVisXL V5.0 Lightning",
            label="Model",
        )
        prompt = gr.Textbox(value="high quality, 4K", label="Prompt (Only required for inpainting)", visible=True)
        size = gr.Slider(value=1024, label="Resize", minimum=0, maximum=1024, step=8, visible=True, interactive=True)
        guidance_scale = gr.Number(value=1.5, label="Guidance Scale", visible=True)
        steps = gr.Number(value=8, label="Steps", precision=0, visible=True)
        
    with gr.Row():   
        open_folder_button = gr.Button("Open Outputs Folder")
        console_info = gr.Textbox(label="Console", interactive=False) 
        iterate_button = gr.Button("Send Result to Input")  # New button  

    run_button.click(
        fn=lambda: "Clearing previous result...",
        inputs=None,
        outputs=console_info,
    ).then(
        fn=clear_result,
        inputs=None,
        outputs=result,
    ).then(
        fn=fill_image,
        inputs=[prompt, input_image, model_selection, guidance_scale, steps],
        outputs=result #add generating image msg

    )
    
    # New click event for iterate button
    iterate_button.click(
        fn=send_to_input,
        inputs=[result], 
        outputs=[input_image, result],
    )
    
    open_folder_button.click(
        fn=open_outputs_folder,
        inputs=None,
        outputs=console_info
    )

    
    input_image.upload(fn=set_img, inputs=input_image).then(fn=resize, inputs=[input_image, size], outputs=[input_image, size, guidance_scale, steps, prompt])
    size.change(fn=resize, inputs=[input_image, size], outputs=[input_image, size, guidance_scale, steps, prompt])


demo.launch(share=False)
